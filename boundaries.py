"""Refino dos pontos de corte.

Dois mecanismos, nesta ordem de prioridade:
 1. Vizinhança: o corte nunca invade a frase anterior nem a seguinte.
 2. Silêncio real: se existe silêncio no intervalo permitido, o corte vai
    para dentro dele — é o que faz o clipe não começar cortando uma sílaba.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import CONFIG, Config
from .segment import Sentence

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")

# Folga mínima para não encostar na fala vizinha.
_GUARD = 0.05


def detect_silences(video: Path, cfg: Config = CONFIG) -> list[tuple[float, float]]:
    """Intervalos de silêncio do áudio inteiro, em segundos."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
        "-af", f"silencedetect=noise={cfg.silence_db}dB:d={cfg.silence_min}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = proc.stderr

    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in log.splitlines():
        if (m := _SIL_START.search(line)):
            pending = max(0.0, float(m.group(1)))
        if (m := _SIL_END.search(line)) and pending is not None:
            end = float(m.group(1))
            if end > pending:
                silences.append((pending, end))
            pending = None
    return silences


def _silence_at(silences: list[tuple[float, float]], t: float) -> tuple[float, float] | None:
    for s, e in silences:
        if s <= t <= e:
            return (s, e)
        if s > t + 5.0:
            break
    return None


def refine(
    clip_start: float,
    clip_end: float,
    prev_end: float | None,
    next_start: float | None,
    media_duration: float,
    silences: list[tuple[float, float]],
    cfg: Config = CONFIG,
) -> tuple[float, float]:
    """Devolve (start, end) refinados. Garante start < end e limites da mídia."""
    # --- limites duros ---
    low = 0.0 if prev_end is None else min(clip_start, prev_end + _GUARD)
    high = media_duration if next_start is None else max(clip_end, next_start - _GUARD)

    start = max(clip_start - cfg.lead_in, low, 0.0)
    end = min(clip_end + cfg.lead_out, high, media_duration)

    # --- encostar no silêncio, sem sair dos limites ---
    if (sil := _silence_at(silences, start)) is not None:
        s, e = sil
        # meio do silêncio, mas nunca depois do início da fala
        cand = min(max((s + e) / 2.0, low), clip_start)
        if abs(cand - start) <= cfg.snap_window:
            start = cand

    if (sil := _silence_at(silences, end)) is not None:
        s, e = sil
        cand = max(min((s + e) / 2.0, high), clip_end)
        if abs(cand - end) <= cfg.snap_window:
            end = cand

    if end - start < 0.5:  # degenerado: volta para o intervalo cru
        start, end = max(clip_start, 0.0), min(clip_end, media_duration)
    return round(start, 3), round(end, 3)


def refine_plan_bounds(
    plan_first: int,
    plan_last: int,
    sentences: list[Sentence],
    media_duration: float,
    silences: list[tuple[float, float]],
    cfg: Config = CONFIG,
) -> tuple[float, float]:
    prev_end = sentences[plan_first - 1].end if plan_first > 0 else None
    next_start = sentences[plan_last + 1].start if plan_last + 1 < len(sentences) else None
    return refine(
        sentences[plan_first].start, sentences[plan_last].end,
        prev_end, next_start, media_duration, silences, cfg,
    )
