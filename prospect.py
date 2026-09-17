"""Garimpo de momentos em transmissões longas.

Uma VOD de 6h não pode ser transcrita inteira: o Whisper large-v3 levaria horas
de GPU para aproveitar uns poucos minutos. Antes de transcrever, o vídeo passa
por uma varredura barata — só o áudio, sem decodificar imagem — que aponta onde
algo aconteceu: o streamer gritou, a plateia reagiu, o jogo virou.

O sinal principal é o quanto o áudio sobe acima do próprio normal daquele
trecho, e não o volume absoluto. Assim funciona igual num canal que fala baixo e
num que vive gritando, e não confunde uma música de fundo alta com reação.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import CONFIG, Config

ENVELOPE_HZ = 8.0          # amostras de volume por segundo
_SAMPLE_RATE = 8000        # o envelope não precisa de banda: só de energia
_BASELINE_SECONDS = 90.0   # janela do "normal" contra o qual o pico é medido
_MIN_EXCESS_DB = 1.5       # abaixo disso nada se destacou: não é momento, é o normal


@dataclass
class Moment:
    start: float
    end: float
    score: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2),
                "score": round(self.score, 2), "reasons": self.reasons}


def loudness_envelope(video: Path, hz: float = ENVELOPE_HZ) -> np.ndarray:
    """Volume RMS por 1/hz segundo, lido em fluxo.

    Decodificar 6h de áudio de uma vez são centenas de MB na memória; aqui o
    ffmpeg entrega em pedaços e cada pedaço vira algumas dezenas de números.
    """
    step = max(1, int(round(_SAMPLE_RATE / hz)))
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
           "-ar", str(_SAMPLE_RATE), "-f", "f32le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    blocks: list[np.ndarray] = []
    tail = b""
    try:
        while True:
            raw = proc.stdout.read(step * 4 * 256) if proc.stdout else b""
            if not raw:
                break
            tail += raw
            usable = len(tail) // (step * 4) * (step * 4)
            if not usable:
                continue
            data = np.frombuffer(tail[:usable], dtype="<f4").reshape(-1, step)
            blocks.append(np.sqrt(np.mean(np.square(data, dtype=np.float64), axis=1)))
            tail = tail[usable:]
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
    return np.concatenate(blocks) if blocks else np.zeros(0)


def _baseline(level: np.ndarray, hz: float) -> np.ndarray:
    """Nível "normal" local, por medianas de blocos — barato e imune a picos."""
    block = max(1, int(round(_BASELINE_SECONDS * hz)))
    edges = np.arange(0, len(level), block)
    medians = np.asarray([float(np.median(level[i:i + block])) for i in edges])
    if len(medians) < 2:
        return np.full(len(level), medians[0] if len(medians) else 0.0)
    centers = edges + block / 2.0
    return np.interp(np.arange(len(level), dtype=float), centers, medians)


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width < 2 or len(values) < width:
        return values
    kernel = np.ones(width) / width
    padded = np.pad(values, (width // 2, width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")[:len(values)]


def excitement_curve(video: Path, visual: dict | None = None, hz: float = ENVELOPE_HZ,
                     envelope: np.ndarray | None = None) -> np.ndarray:
    """Curva de "algo está acontecendo", uma amostra a cada 1/hz segundo."""
    envelope = loudness_envelope(video, hz) if envelope is None else envelope
    if len(envelope) < int(hz * 8):
        return np.zeros(len(envelope))
    level = 20.0 * np.log10(envelope + 1e-6)
    excess = level - _baseline(level, hz)
    # Reação é sustentada, não um estalo: meio segundo de média já separa um
    # grito de um clique de mouse, e 3 s privilegiam a sequência inteira.
    curve = _smooth(np.clip(excess, 0.0, None), max(2, int(round(hz * 3.0))))

    changes = (visual or {}).get("scene_changes") or []
    if changes:
        # Corte de cena junto com pico de áudio costuma marcar o replay, a morte,
        # o gol — o momento que o editor humano escolheria.
        bonus = np.zeros(len(curve))
        span = int(round(hz * 5.0))
        for t in changes:
            i = int(round(float(t) * hz))
            bonus[max(0, i - span):i + span] += 1.0
        curve = curve + np.clip(bonus, 0, 3) * 0.6
    return curve


def find_moments(video: Path, duration: float, cfg: Config = CONFIG,
                 visual: dict | None = None, count: int = 30,
                 envelope: np.ndarray | None = None) -> list[Moment]:
    """Até `count` momentos promissores, do melhor para o pior, sem sobreposição."""
    hz = ENVELOPE_HZ
    curve = excitement_curve(video, visual, hz, envelope)
    if not len(curve):
        return []
    span = max(cfg.min_duration, min(cfg.target_duration, cfg.max_duration))
    # O clímax fica perto do fim do trecho: a montagem precisa da preparação.
    lead, tail = span * 0.7, span * 0.3
    floor = max(2.0, float(np.percentile(curve, 92)))

    order = np.argsort(curve)[::-1]
    picked: list[Moment] = []
    for index in order:
        value = float(curve[index])
        # Transmissão sem reação nenhuma (tutorial, música, jogo silencioso) não
        # tem momento a garimpar: melhor devolver nada e deixar quem chamou
        # espalhar sondagens do que inventar um recorte sem motivo.
        if value < _MIN_EXCESS_DB:
            break
        if value < floor and picked:
            break
        peak = float(index) / hz
        if any(abs(peak - (m.start + m.end) / 2) < span * 0.8 for m in picked):
            continue
        start = max(0.0, min(peak - lead, max(0.0, duration - span)))
        end = min(duration, max(start + cfg.min_duration, peak + tail))
        if end - start < cfg.min_duration:
            continue
        reasons = ["pico de áudio acima do normal do trecho"]
        if (visual or {}).get("scene_changes"):
            if any(abs(float(t) - peak) <= 5.0 for t in visual["scene_changes"]):
                reasons.append("mudança de cena junto com o pico")
        picked.append(Moment(start, end, value, reasons))
        if len(picked) >= count:
            break
    return picked


def analysis_windows(moments: list[Moment], duration: float,
                     cfg: Config = CONFIG) -> list[tuple[float, float]]:
    """Janelas a transcrever: cada momento mais margem, com sobreposições unidas.

    A margem existe porque o corte precisa de fala inteira em volta do pico — o
    gancho quase sempre começa antes do grito.
    """
    if not moments:
        return []
    margin = max(0.0, cfg.prospect_margin)
    spans = sorted((max(0.0, m.start - margin), min(duration, m.end + margin)) for m in moments)
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a > 1.0]


def fallback_windows(duration: float, cfg: Config = CONFIG, count: int = 12) -> list[tuple[float, float]]:
    """Amostragem regular quando o áudio não denuncia nenhum pico.

    Acontece em transmissão sem reação (tutorial, música, jogo silencioso). Vale
    mais espalhar sondagens pelo vídeo do que transcrever seis horas inteiras.
    """
    span = max(cfg.min_duration, min(cfg.target_duration, cfg.max_duration)) + cfg.prospect_margin
    count = max(1, min(count, int(duration // max(1.0, span))))
    centers = np.linspace(duration * 0.05, duration * 0.95, count)
    return [(round(max(0.0, c - span / 2), 3), round(min(duration, c + span / 2), 3)) for c in centers]


def should_prospect(duration: float, cfg: Config = CONFIG) -> bool:
    return duration > cfg.prospect_after_minutes * 60.0
