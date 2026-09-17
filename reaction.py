"""Reação e comparação: dois vídeos num único quadro vertical.

Dois arranjos. "stacked" é o duo: em cima o que está sendo reagido, embaixo quem
reage. "side" é o antes e depois: esquerda e direita, com um divisor no meio,
para tutoriais, reformas e maquiagem. Os campos continuam `top` e `bottom`; no
lado a lado eles valem esquerda e direita.

As duas fontes chegam de plataformas diferentes, com resolução, cadência e volume
próprios — por isso cada metade passa pela normalização comum antes de juntar, e
o som é somado com limitador em vez de média, que deixaria tudo baixo.

A legenda falada transcreve só uma das metades: legendar as duas mistura vozes
numa linha só e ninguém sabe quem disse o quê.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .captions import _ts, build_ass
from .config import Config
from .download import DownloadError, is_supported_url
from .editor import atomic_json
from .montage import (PLAY_H, PLAY_W, MusicOptions, add_music, ass_document, ffmpeg, fetch_source,
                      is_tiktok_url, music_path, normalize, text_literal)
from .probe import probe

# Metade de cima e metade de baixo do canvas de referência.
_HALF = PLAY_H // 2
# Espessura do divisor do lado a lado, no canvas de referência.
_DIVIDER = 6


class Side(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=600)
    label: str = Field(default="", max_length=32)
    start: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    volume: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def supported(cls, value):
        if not (is_tiktok_url(value) or is_supported_url(value)):
            raise ValueError("Use um link de vídeo do TikTok, YouTube, Twitch ou Kick")
        return value

    @field_validator("label")
    @classmethod
    def printable(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("O rótulo não pode ter quebra de linha")
        return value


class Reaction(MusicOptions):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    headline: str = Field(default="", max_length=80)
    top: Side
    bottom: Side
    layout: Literal["stacked", "side"] = "stacked"
    # Quanto tempo do encontro entra no Short. `None` usa o menor dos dois vídeos.
    duration: float | None = Field(default=None, ge=1, le=180, allow_inf_nan=False)
    fit: Literal["cover", "contain"] = "cover"
    normalize_audio: bool = True
    # Qual metade ganha legenda da própria fala.
    captions: Literal["none", "top", "bottom"] = "none"

    @field_validator("headline")
    @classmethod
    def tidy(cls, value):
        return " ".join(value.split())


def side_name(layout: str, name: str) -> str:
    if layout == "side":
        return "da esquerda" if name == "top" else "da direita"
    return "de cima" if name == "top" else "de baixo"


def labels_ass(spec: Reaction, duration: float, path: Path) -> Path:
    """Título no topo e o rótulo de cada metade, parados durante todo o vídeo."""
    styles = [
        "Style: Title,Arial,58,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,4,2,8,60,60,0,1",
        "Style: Tag,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,3,2,7,0,0,0,1",
        "Style: Badge,Arial,62,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,4,2,8,0,0,0,1",
    ]
    end = _ts(duration)
    events: list[str] = []
    if spec.headline:
        events.append(f"Dialogue: 1,0:00:00.00,{end},Title,,0,0,0,,"
                      f"{{\\pos({PLAY_W // 2},70)\\an8}}{text_literal(spec.headline)}\n")
    if spec.layout == "side":
        # Rótulo centrado em cada coluna, abaixo do título quando ele existe.
        y = 200 if spec.headline else 90
        for side, x in ((spec.top, PLAY_W // 4), (spec.bottom, 3 * PLAY_W // 4)):
            if side.label:
                events.append(f"Dialogue: 2,0:00:00.00,{end},Badge,,0,0,0,,"
                              f"{{\\pos({x},{y})\\an8}}{text_literal(side.label.upper())}\n")
    else:
        # O rótulo de baixo fica logo abaixo da linha de junção, não no pé da tela:
        # no pé ele briga com a barra de interface do aplicativo de vídeo. O de cima
        # desce quando há título, senão os dois disputam o mesmo canto.
        top_y = 150 if spec.headline else 40
        for side, y in ((spec.top, top_y), (spec.bottom, _HALF + 40)):
            if side.label:
                events.append(f"Dialogue: 2,0:00:00.00,{end},Tag,,0,0,0,,"
                              f"{{\\pos(48,{y})}}{text_literal(side.label)}\n")
    path.write_text(ass_document(styles, events), encoding="utf-8")
    return path


def transcribe_side(clip: Path, cache: Path):
    """Separado para os testes trocarem o Whisper por palavras prontas."""
    from .transcribe import transcribe
    return transcribe(clip, cache, Config())


def speech_ass(spec: Reaction, clip: Path, duration: float, width: int, height: int,
               work: Path) -> tuple[bool, list[str]]:
    """Legenda palavra a palavra da metade escolhida. Devolve se há texto e avisos."""
    try:
        transcript = transcribe_side(clip, work / f"{spec.captions}-transcript.json")
    except Exception as exc:
        raise RuntimeError(f"Legenda: não foi possível transcrever o vídeo "
                           f"{side_name(spec.layout, spec.captions)} ({type(exc).__name__}: {exc})") from exc
    if not transcript.words:
        return False, [f"Legenda: nenhuma fala detectada no vídeo {side_name(spec.layout, spec.captions)}."]
    cfg = Config(out_width=width, out_height=height, caption_size=64, caption_font="Arial",
                 # No duo a junção é o centro da tela e fica entre as duas caras;
                 # no lado a lado o centro é o divisor, então a legenda desce.
                 caption_position="middle" if spec.layout == "stacked" else "bottom")
    build_ass(transcript.words, 0.0, duration, work / "speech.ass", cfg)
    return True, []


def render_reaction(spec: Reaction, sources: dict[str, Path], directory: Path,
                    progress=lambda *_: None, width: int = PLAY_W, height: int = PLAY_H) -> dict:
    directory = directory.resolve()
    work = directory / "reaction-work"
    clips = directory / "clips"
    work.mkdir(parents=True, exist_ok=True)
    clips.mkdir(parents=True, exist_ok=True)
    side_by_side = spec.layout == "side"
    tile_w, tile_h = (width // 2, height) if side_by_side else (width, height // 2)
    music = music_path(spec.music) if spec.music else None

    sides = {"top": spec.top, "bottom": spec.bottom}
    infos = {name: probe(sources[name]) for name in sides}
    restante = {}
    for name, side in sides.items():
        left = infos[name].duration - side.start
        if left < 1.0:
            raise ValueError(f"Vídeo {side_name(spec.layout, name)}: "
                             f"o início escolhido deixa menos de 1 segundo de vídeo")
        restante[name] = left
    duration = min(spec.duration or min(restante.values()), *restante.values())
    # Casar a duração com o quadro evita um último frame parcial na junção.
    duration = max(1.0, math.floor(duration * 30 + 1e-6) / 30)

    for index, (name, side) in enumerate(sides.items()):
        progress(f"preparando vídeo {side_name(spec.layout, name)}", .30 + index * .15)
        normalize(sources[name], f"{name}.mov", tile_w, tile_h, side.start, duration, work,
                  cover=spec.fit == "cover", volume=side.volume,
                  has_audio=infos[name].has_audio)

    warnings = ["Use vídeos próprios ou autorizados: juntar não cria licença sobre o "
                "material de terceiros."]
    overlays = "ass=labels.ass"
    if spec.captions != "none":
        progress(f"transcrevendo a fala do vídeo {side_name(spec.layout, spec.captions)}", .62)
        has_text, notes = speech_ass(spec, work / f"{spec.captions}.mov", duration, width, height, work)
        warnings += notes
        if has_text:
            overlays += ",ass=speech.ass"

    progress("juntando e somando o áudio", .80)
    labels_ass(spec, duration, work / "labels.ass")
    if side_by_side:
        divider = max(2, round(_DIVIDER * width / PLAY_W) // 2 * 2)
        video = (f"[0:v][1:v]hstack=inputs=2,drawbox=x=iw/2-{divider // 2}:y=0:w={divider}:h=ih:"
                 f"color=white@0.9:t=fill[joined]")
    else:
        video = "[0:v][1:v]vstack=inputs=2[joined]"
    # normalize=0 mantém o nível de cada fonte; a média do amix deixaria as duas
    # baixas justamente quando só uma está falando. O limitador segura o pico.
    graph = (f"{video};[joined]{overlays}[v];"
             "[0:a][1:a]amix=inputs=2:duration=first:normalize=0"
             + (",loudnorm=I=-16:TP=-1.5:LRA=11" if spec.normalize_audio else "")
             + ",alimiter=limit=0.89:level=false[a]")
    temporary = clips / "reaction-rendering.mp4"
    ffmpeg(["-i", "top.mov", "-i", "bottom.mov", "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(temporary)], work)
    if music:
        progress("mixando a música", .90)
        mixed = clips / "reaction-music.mp4"
        add_music(temporary, music, mixed, duration, spec, work)
        mixed.replace(temporary)

    actual = probe(temporary)
    if abs(actual.duration - duration) > .15 or not actual.has_audio:
        raise RuntimeError("A montagem não passou na verificação de duração e áudio")
    final = clips / "reaction.mp4"
    temporary.replace(final)
    ffmpeg(["-i", str(final), "-frames:v", "1", "-update", "1", str(clips / "reaction-cover.jpg")], work)

    names = {"top": "Esquerda", "bottom": "Direita"} if side_by_side else {"top": "Cima", "bottom": "Baixo"}
    creditos = "\n".join(f"{names[n]}: {s.label or 'sem rótulo'} — {s.url}" for n, s in sides.items())
    fallback = ("Antes", "Depois") if side_by_side else ("Original", "Reação")
    titulo = spec.headline or f"{spec.top.label or fallback[0]} + {spec.bottom.label or fallback[1]}"
    return {"kind": "reaction", "source_duration": duration, "source_resolution": f"{width}×{height}",
            "provider": "montagem de reação", "headline": spec.headline, "layout": spec.layout,
            "captions": spec.captions, "music": bool(music),
            "sides": {n: {"url": s.url, "label": s.label, "start": s.start, "volume": s.volume}
                      for n, s in sides.items()},
            "clips": [{"index": 1, "revision": 0, "file": "reaction.mp4",
                       "thumbnail": "reaction-cover.jpg", "title": titulo,
                       "text": creditos, "description": creditos,
                       "actual_duration": actual.duration, "source_start": 0,
                       "source_end": duration, "provider": "montagem de reação",
                       "warnings": warnings}]}


def process_reaction(settings: dict, directory: Path, progress=lambda *_: None) -> dict:
    started = time.time()
    spec = Reaction.model_validate(settings["reaction"])
    sources: dict[str, Path] = {}
    for index, (name, side) in enumerate({"top": spec.top, "bottom": spec.bottom}.items()):
        progress(f"baixando vídeo {index + 1}/2", index * .12)
        folder = directory / "source" / name
        marker = folder / "download.json"
        cached = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        path = folder / cached.get("file", "missing")
        if cached.get("url") != side.url or not path.is_file() \
                or not path.resolve().is_relative_to(folder.resolve()):
            try:
                path = fetch_source(side.url, folder)
            except Exception as exc:
                raise DownloadError(f"Vídeo {side_name(spec.layout, name)}: {exc}") from exc
            atomic_json(marker, {"url": side.url, "file": path.name})
        sources[name] = path
    manifest = render_reaction(spec, sources, directory, progress)
    manifest["elapsed_seconds"] = round(time.time() - started, 2)
    return manifest
