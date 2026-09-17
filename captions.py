"""Legendas ASS com destaque palavra a palavra (estilo karaokê).

Um evento Dialogue por palavra: o grupo inteiro fica visível e a palavra
corrente troca de cor. É o formato que o libass renderiza igual em qualquer
máquina, sem depender de fonte instalada no player.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from .config import CONFIG, Config
from .transcribe import Word

MAX_GROUP_SECONDS = 2.6
MIN_EVENT = 0.06


@dataclass
class Group:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def _ts(seconds: float) -> str:
    ticks = max(0, round(seconds * 100))
    h, ticks = divmod(ticks, 360000)
    m, ticks = divmod(ticks, 6000)
    return f"{h:d}:{m:02d}:{ticks // 100:02d}.{ticks % 100:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _phonemes(text: str) -> list[str]:
    """Readable grapheme-to-phoneme groups for progressive karaoke timing."""
    parts=re.findall(r"[^aeiouáéíóúâêôãõàü]*[aeiouáéíóúâêôãõàü]+(?:[mnrsz](?=$|[^aeiouáéíóúâêôãõàü]))?|[^aeiouáéíóúâêôãõàü]+$",text,re.I)
    return parts or [text]


def group_words(words: list[Word], cfg: Config = CONFIG) -> list[Group]:
    groups: list[Group] = []
    buf: list[Word] = []
    for w in words:
        if buf:
            # Estimate width conservatively so long words form smaller groups.
            chars = len(" ".join(x.text for x in [*buf, w]))
            too_long = len(buf) >= cfg.caption_max_words or chars * cfg.caption_size * (cfg.out_height / 1920) * 0.60 > cfg.out_width * 0.76
            too_slow = (w.end - buf[0].start) > MAX_GROUP_SECONDS
            big_gap = (w.start - buf[-1].end) > 0.55
            if too_long or too_slow or big_gap:
                groups.append(Group(list(buf)))
                buf.clear()
        buf.append(w)
        if buf[-1].text[-1:] in ".?!…":
            groups.append(Group(list(buf)))
            buf.clear()
    if buf:
        groups.append(Group(list(buf)))
    return groups


def _header(cfg: Config) -> str:
    align = {"bottom": 2, "middle": 5, "top": 8}[cfg.caption_position]
    margin = round(cfg.out_height * (0.12 if cfg.caption_position == "top" else 0.22))
    size = round(cfg.caption_size * cfg.out_height / 1920)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cfg.out_width}
PlayResY: {cfg.out_height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cfg.caption_font},{size},{cfg.caption_primary},&H000000FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,5,2,{align},80,130,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[Word], clip_start: float, clip_end: float,
              out_path: Path, cfg: Config = CONFIG) -> Path:
    """Gera o .ass com tempos RELATIVOS ao início do clipe."""
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    groups = group_words(inside, cfg)

    span = clip_end - clip_start
    lines = [_header(cfg)]
    for gi, g in enumerate(groups):
        tokens = [_escape(w.text) for w in g.words]
        # teto do grupo: começo do próximo grupo (eventos nunca se sobrepõem)
        nxt = groups[gi + 1].start - clip_start if gi + 1 < len(groups) else span
        ceiling = min(nxt, span)

        for i, w in enumerate(g.words):
            start = max(w.start - clip_start, 0.0)
            end = max(w.end - clip_start, start + MIN_EVENT)
            if i + 1 < len(g.words):
                # segura até a palavra seguinte acender: sem piscada entre palavras
                end = min(ceiling, g.words[i + 1].start - clip_start)
            else:
                end = max(end, min(end + 0.20, ceiling))
            if start >= span:
                continue
            end = min(end, ceiling)
            if round(end * 100) <= round(start * 100):
                continue

            rendered = []
            for j, tok in enumerate(tokens):
                if j == i and cfg.caption_style == "karaoke":
                    phonemes=_phonemes(tok); ticks=max(1,round((end-start)*100)); weight=sum(max(1,len(x)) for x in phonemes)
                    karaoke="".join(f"{{\\kf{max(1,round(ticks*max(1,len(part))/weight))}}}{part}" for part in phonemes)
                    # O resto do grupo ainda "não foi cantado" para o karaokê e sairia na
                    # cor secundária (vermelha); `\2c` faz as próximas palavras ficarem brancas.
                    rendered.append(f"{{\\c{cfg.caption_highlight}}}{karaoke}"
                                    f"{{\\c{cfg.caption_primary}\\2c{cfg.caption_primary}}}")
                else:
                    rendered.append(tok)
            text = " ".join(rendered)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{text}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def build_srt(words: list[Word], clip_start: float, clip_end: float,
              out_path: Path, cfg: Config = CONFIG) -> Path:
    def stamp(t: float) -> str:
        ms = max(0, round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        return f"{h:02d}:{m:02d}:{ms // 1000:02d},{ms % 1000:03d}"
    groups = group_words([w for w in words if w.end > clip_start and w.start < clip_end], cfg)
    lines = []
    for i, group in enumerate(groups):
        end = min(clip_end, group.end, groups[i + 1].start if i + 1 < len(groups) else clip_end)
        start = max(clip_start, group.start)
        if end > start:
            lines.append(f"{len(lines) + 1}\n{stamp(start - clip_start)} --> {stamp(end - clip_start)}\n" + " ".join(w.text for w in group.words) + "\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
