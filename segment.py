"""Palavras -> frases numeradas.

A frase é a unidade atômica do corte. O LLM nunca escolhe segundos:
escolhe IDs de frase, e o código deriva os segundos. É isso que elimina
alucinação de duração e corte no meio da frase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .transcribe import Transcript, Word

TERMINATORS = ".?!…"
# Pausa longa também fecha frase, para transcrição sem pontuação.
PAUSE_BREAK = 0.70
# Abreviações comuns que NÃO devem fechar a frase (pt/en).
ABBREV = {
    "sr", "sra", "dr", "dra", "prof", "profa", "etc", "ex", "vs", "aprox",
    "mr", "mrs", "ms", "st", "jr", "ph", "inc", "ltd", "fig", "vol", "no", "nº",
}
_WORDISH = re.compile(r"[\wÀ-ÿ]")


@dataclass
class Sentence:
    id: int
    start: float
    end: float
    text: str
    words: list[Word]
    speaker: str | None = None
    pause_before: float = 0.0
    emphasis: float = 0.0
    topic_boundary: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def word_count(self) -> int:
        return len(self.words)


def _is_abbrev(token: str) -> bool:
    core = token.rstrip(TERMINATORS + ",;:").lower()
    return core in ABBREV or (len(core) == 1 and core.isalpha())


def build_sentences(tr: Transcript, pause_break: float = PAUSE_BREAK) -> list[Sentence]:
    sentences: list[Sentence] = []
    buf: list[Word] = []

    def flush() -> None:
        if not buf:
            return
        text = " ".join(w.text for w in buf)
        text = re.sub(r"\s+([,.;:!?…])", r"\1", text).strip()
        if _WORDISH.search(text):
            previous_end = sentences[-1].end if sentences else 0.0
            speakers = [w.speaker for w in buf if w.speaker]
            speaker = max(set(speakers), key=speakers.count) if speakers else None
            punctuation = text.count("?") + text.count("!")
            confidence = sum(w.prob for w in buf) / len(buf)
            acoustic = [w.energy for w in buf if w.energy is not None]
            sentences.append(Sentence(
                id=len(sentences),
                start=buf[0].start,
                end=buf[-1].end,
                text=text,
                words=list(buf),
                speaker=speaker,
                pause_before=max(0.0, buf[0].start - previous_end),
                emphasis=min(1.0, punctuation * 0.25 + (0.1 if confidence > 0.92 else 0.0) +
                             (sum(acoustic)/len(acoustic)*.55 if acoustic else 0.0)),
                topic_boundary=bool(sentences and (buf[0].start - previous_end > 1.6 or
                                    (speaker and speaker != sentences[-1].speaker))),
            ))
        buf.clear()

    for i, w in enumerate(tr.words):
        buf.append(w)
        nxt = tr.words[i + 1] if i + 1 < len(tr.words) else None

        ends_punct = w.text[-1:] in TERMINATORS and not _is_abbrev(w.text)
        long_pause = nxt is not None and (nxt.start - w.end) >= pause_break
        last = nxt is None

        if ends_punct or long_pause or last:
            flush()

    flush()
    return sentences


def transcript_outline(sentences: list[Sentence]) -> str:
    """Bloco de texto numerado que vai para o LLM. Sem timestamps de propósito."""
    lines = []
    for s in sentences:
        context = []
        if s.speaker:
            context.append(s.speaker)
        if s.pause_before >= 0.7:
            context.append(f"pausa {s.pause_before:.1f}s")
        if s.topic_boundary:
            context.append("mudança")
        tag = f" <{' | '.join(context)}>" if context else ""
        lines.append(f"[{s.id}] ({s.duration:.1f}s){tag} {s.text}")
    return "\n".join(lines)


def span_bounds(sentences: list[Sentence], first: int, last: int) -> tuple[float, float]:
    """Segundos reais de um intervalo de frases. Única fonte de verdade da duração."""
    first = max(0, min(first, len(sentences) - 1))
    last = max(first, min(last, len(sentences) - 1))
    return sentences[first].start, sentences[last].end


def fit_span(
    sentences: list[Sentence],
    first: int,
    last: int,
    min_duration: float,
    max_duration: float,
) -> tuple[int, int] | None:
    """Ajusta o intervalo para caber na faixa de duração, sempre em frases inteiras.

    Encurta pelo fim (o começo carrega o gancho). Se ficar curto, estende pelo fim
    e, em último caso, pelo começo. Devolve None se não houver como caber.
    """
    n = len(sentences)
    first = max(0, min(first, n - 1))
    last = max(first, min(last, n - 1))

    # Longo demais: corta pelo fim, mantendo pelo menos uma frase.
    while last > first and (span_bounds(sentences, first, last)[1] - span_bounds(sentences, first, last)[0]) > max_duration:
        last -= 1

    start, end = span_bounds(sentences, first, last)
    if end - start > max_duration:
        return None  # uma única frase já passa do limite

    # Curto demais: estende pelo fim, depois pelo começo.
    while end - start < min_duration and last < n - 1:
        cand_start, cand_end = span_bounds(sentences, first, last + 1)
        if cand_end - cand_start > max_duration:
            break
        last += 1
        start, end = cand_start, cand_end

    while end - start < min_duration and first > 0:
        cand_start, cand_end = span_bounds(sentences, first - 1, last)
        if cand_end - cand_start > max_duration:
            break
        first -= 1
        start, end = cand_start, cand_end

    if end - start < min_duration:
        return None
    return first, last
