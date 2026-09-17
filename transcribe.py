"""Transcrição com timestamps por palavra (faster-whisper).

Por que palavra a palavra: o corte precisa cair em fronteira de frase real.
Timestamp por segmento tem granularidade grossa demais e é exatamente
de onde saem os cortes no meio da palavra.
"""
from __future__ import annotations

import json
import os
import subprocess
import re
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass
from pathlib import Path
from functools import lru_cache
import numpy as np

from .config import CONFIG, Config


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0
    speaker: str | None = None
    energy: float | None = None
    pitch: float | None = None


@dataclass
class Transcript:
    language: str
    duration: float
    words: list[Word]
    speakers: list[str] | None = None
    name_corrections: list[dict] | None = None

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "language": self.language,
            "duration": self.duration,
            "words": [asdict(w) for w in self.words],
            "speakers": self.speakers or [],
            "name_corrections": self.name_corrections or [],
        }, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "Transcript":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            language=data["language"],
            duration=data["duration"],
            words=[Word(**w) for w in data["words"]],
            speakers=data.get("speakers") or [],
            name_corrections=data.get("name_corrections") or [],
        )


def _resolve_device(cfg: Config) -> tuple[str, str]:
    device, compute = cfg.whisper_device, cfg.whisper_compute
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute == "default":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def cache_signature(video: Path, cfg: Config, context: str = "") -> dict:
    stat = video.stat()
    return {"source": str(video.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "model": cfg.whisper_model, "language": cfg.language, "diarization": cfg.diarization,
            # O feixe efetivo, não o configurado: `0` quer dizer "decida pelo
            # dispositivo", e trocar de máquina muda o resultado da decodificação.
            "beam": _beam_size(_resolve_device(cfg)[0], cfg), "context": context[:4000]}


@lru_cache(maxsize=1)
def _model(name: str, device: str, compute: str, threads: int = 0):
    from faster_whisper import WhisperModel
    # `cpu_threads=0` deixa a escolha com o ctranslate2, que é conservador; numa
    # máquina sem GPU a transcrição é o trecho mais longo do processamento e
    # vale usar os núcleos que existem.
    # Mais threads significam mais buffers do MKL. Numa máquina ocupada isso passa
    # a falhar na alocação, e ficar sem transcrição por causa de desempenho é o
    # pior dos dois mundos: cai para a escolha da biblioteca e segue.
    for attempt in (threads, 0, 1):
        try:
            return WhisperModel(name, device=device, compute_type=compute, cpu_threads=attempt)
        except RuntimeError as exc:
            if "alloc" not in str(exc).lower() or attempt == 1:
                raise
    raise RuntimeError("não foi possível carregar o modelo de transcrição")


def _decode_threads(device: str, cfg: Config) -> int:
    if cfg.whisper_threads:
        return cfg.whisper_threads
    return 0 if device == "cuda" else max(1, (os.cpu_count() or 4))


def _beam_size(device: str, cfg: Config) -> int:
    """Busca em feixe é barata na GPU e cara no CPU.

    Medido em 60 s de fala em português com o modelo `small` em int8: feixe 5
    custou 17,1 s e feixe 2 custou 11,4 s, com texto idêntico palavra por palavra.
    Em áudio ruidoso a diferença pode aparecer — daí o parâmetro continuar aberto.
    """
    if cfg.whisper_beam:
        return cfg.whisper_beam
    return 5 if device == "cuda" else 2


def _diarize(video: Path, words: list[Word], cfg: Config) -> list[str]:
    """Assign pyannote speaker turns when configured; transcription still works without it."""
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not cfg.diarization or not token or not words:
        return []
    try:
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
        diarization = pipeline(str(video))
        turns = [(float(turn.start), float(turn.end), str(speaker))
                 for turn, _, speaker in diarization.itertracks(yield_label=True)]
        for word in words:
            middle = (word.start + word.end) / 2
            match = min(turns, key=lambda t: 0 if t[0] <= middle <= t[1]
                        else min(abs(middle - t[0]), abs(middle - t[1])), default=None)
            if match:
                word.speaker = match[2]
        return sorted({w.speaker for w in words if w.speaker})
    except Exception:
        return []


def _acoustic_context(video: Path, words: list[Word], cfg: Config) -> list[str]:
    """Extract energy/pitch and provide conservative local speaker clustering."""
    if not words:
        return []
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                           "-ar", "16000", "-f", "f32le", "pipe:1"], capture_output=True)
    audio = np.frombuffer(proc.stdout, dtype="<f4")
    if not len(audio):
        return []
    features = []
    for word in words:
        a, b = max(0, int(word.start*16000)), min(len(audio), int(word.end*16000))
        chunk = audio[a:b]
        if len(chunk) < 160:
            features.append([0., 0., 0., 0.]); continue
        chunk = chunk - chunk.mean()
        rms = float(np.sqrt(np.mean(chunk*chunk)) + 1e-9)
        signs = float(np.mean(np.abs(np.diff(np.signbit(chunk)))))
        window = chunk[:min(len(chunk), 8192)] * np.hanning(min(len(chunk), 8192))
        spectrum = np.abs(np.fft.rfft(window)) + 1e-9
        freqs = np.fft.rfftfreq(len(window), 1/16000)
        centroid = float((spectrum*freqs).sum()/spectrum.sum()/8000)
        voice = (freqs >= 70) & (freqs <= 350)
        pitch = float(freqs[voice][np.argmax(spectrum[voice])]) if np.any(voice) else 0.
        word.energy, word.pitch = rms, pitch or None
        features.append([np.log(rms), signs, centroid, pitch/350])
    matrix = np.asarray(features)
    # Normalize emphasis against this video's own voice, not an arbitrary loudness.
    energies = matrix[:, 0]
    med, spread = np.median(energies), max(.1, np.std(energies))
    for word, value in zip(words, energies):
        word.energy = float(np.clip(.5 + (value-med)/(4*spread), 0, 1))
    if not cfg.diarization or len(words) < 12:
        return []
    # Aggregate into speech runs; word phonemes are too short for speaker identity.
    runs, current = [], []
    for i, word in enumerate(words):
        if current and (word.start - words[current[-1]].end > .9 or word.end - words[current[0]].start > 3):
            runs.append(current); current = []
        current.append(i)
    if current: runs.append(current)
    if len(runs) < 4:
        return []
    vectors = np.asarray([np.median(matrix[idxs], axis=0) for idxs in runs])
    scale = np.std(vectors, axis=0); scale[scale < .05] = 1
    vectors = (vectors - np.mean(vectors, axis=0))/scale
    # Deterministic two-centroid clustering, accepted only with strong separation.
    centers = np.asarray([vectors[0], vectors[np.argmax(np.linalg.norm(vectors-vectors[0], axis=1))]])
    for _ in range(12):
        labels = np.argmin(((vectors[:, None]-centers[None, :])**2).sum(axis=2), axis=1)
        updated = np.asarray([vectors[labels == k].mean(axis=0) if np.any(labels == k) else centers[k] for k in range(2)])
        if np.allclose(updated, centers): break
        centers = updated
    own = np.sqrt(((vectors-centers[labels])**2).sum(axis=1)).mean()
    separation = np.linalg.norm(centers[0]-centers[1])
    if separation < max(2.5, own*2.2) or min(np.sum(labels == 0), np.sum(labels == 1)) < 2:
        return []
    for idxs, label in zip(runs, labels):
        for i in idxs: words[i].speaker = f"SPEAKER_{label:02d}"
    return sorted({w.speaker for w in words if w.speaker})


def _correct_names(words: list[Word], context: str) -> list[dict]:
    """Correct only low-confidence near-matches against explicit metadata names."""
    candidates = {x for x in re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ.-]{2,}\b", context)
                  if not x.isupper() and len(x) >= 4}
    corrections = []
    for word in words:
        raw = word.text.strip(".,!?;:")
        if word.prob >= .82 or len(raw) < 4:
            continue
        matches = [(SequenceMatcher(None, raw.casefold(), name.casefold()).ratio(), name)
                   for name in candidates if name[0].casefold() == raw[0].casefold() and abs(len(name)-len(raw)) <= 3]
        if matches and max(matches)[0] >= .84:
            _, replacement = max(matches)
            suffix = word.text[len(word.text.rstrip(".,!?;:")):]
            corrections.append({"from": raw, "to": replacement, "start": round(word.start, 3)})
            word.text = replacement + suffix
    return corrections


def _window_audio(video: Path, start: float, end: float) -> np.ndarray:
    """PCM mono 16 kHz de um trecho, no formato que o faster-whisper aceita direto."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}",
                           "-t", f"{max(0.0, end - start):.3f}", "-i", str(video),
                           "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
                          capture_output=True)
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)


def _decode(model, audio, cfg: Config, context: str, beam: int):
    return model.transcribe(
        audio,
        language=cfg.language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 350},
        beam_size=beam,
        condition_on_previous_text=False,
        initial_prompt=context[:1000] or None,
    )


def _collect(segments, offset: float = 0.0) -> list[Word]:
    words: list[Word] = []
    for seg in segments:
        for w in (seg.words or []):
            text = w.word.strip()
            if not text:
                continue
            probability = getattr(w, "probability", None)
            words.append(Word(start=float(w.start) + offset, end=float(w.end) + offset, text=text,
                              prob=float(probability) if probability is not None else 1.0))
    return words


def transcribe(video: Path, cache: Path | None = None, cfg: Config = CONFIG, context: str = "",
               windows: list[tuple[float, float]] | None = None) -> Transcript:
    """Transcreve `video`. Reaproveita `cache` (JSON) se já existir.

    `windows` limita a transcrição a trechos (início, fim) em segundos do
    original. É o que torna viável uma VOD de 6h: transcreve-se meia hora de
    momentos garimpados em vez do dia inteiro de transmissão.
    """
    signature = cache_signature(video, cfg, context)
    if windows:
        signature["windows"] = [[round(a, 2), round(b, 2)] for a, b in windows]
    if cache and Path(cache).exists():
        try:
            data = json.loads(Path(cache).read_text(encoding="utf-8"))
            if data.get("signature") == signature:
                return Transcript.from_json(Path(cache))
        except (ValueError, KeyError, TypeError):
            pass

    device, compute = _resolve_device(cfg)
    model = _model(cfg.whisper_model, device, compute, _decode_threads(device, cfg))
    beam = _beam_size(device, cfg)

    if windows:
        from .probe import probe as _probe
        words, language = [], cfg.language or ""
        for start, end in windows:
            audio = _window_audio(video, start, end)
            if not len(audio):
                continue
            segments, info = _decode(model, audio, cfg, context, beam)
            words.extend(_collect(segments, offset=float(start)))
            language = language or info.language
        words.sort(key=lambda w: w.start)
        duration = _probe(video).duration
    else:
        segments, info = _decode(model, str(video), cfg, context, beam)
        words = _collect(segments)
        language, duration = info.language, float(info.duration)

    corrections = _correct_names(words, context)
    speakers = _acoustic_context(video, words, cfg)
    precise = _diarize(video, words, cfg)
    if precise:
        speakers = precise
    tr = Transcript(language=language, duration=duration, words=words, speakers=speakers,
                    name_corrections=corrections)
    if cache:
        tr.to_json(Path(cache))
        data = json.loads(Path(cache).read_text(encoding="utf-8"))
        data["signature"] = signature
        temp = Path(cache).with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(cache)
    return tr
