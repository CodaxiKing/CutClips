"""Three to five TikTok clips, one customizable vertical ranking."""
from __future__ import annotations

import json
import math
import re
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator
from .captions import _ts
from .config import Config
from .download import DownloadError
from .montage import download_tiktok, ffmpeg, text_literal, tiktok_duration
from .editor import atomic_json
from .probe import probe


# Cada posição mostra no máximo este trecho; sem duração escolhida, usa o que couber.
MAX_CLIP = 15.0


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=600)
    name: str = Field(min_length=1, max_length=32)
    start: float = Field(default=0, ge=0, le=600, allow_inf_nan=False)
    duration: float | None = Field(default=None, ge=0.5, le=MAX_CLIP, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def tiktok_url(cls, value):
        u = urlparse(value)
        if u.scheme != "https" or u.username or u.password or u.port or u.fragment:
            raise ValueError("Use um link HTTPS de vídeo do TikTok")
        normal = u.hostname in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"} and re.fullmatch(r"/(?:@[\w.\-]+/video/\d+|t/[\w-]+)/?", u.path)
        short = u.hostname in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[\w-]+/?", u.path)
        if not (normal or short):
            raise ValueError("Cole o link de um vídeo do TikTok, não de perfil, live ou playlist")
        return value

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Informe um nome sem quebras de linha")
        return value


class TopFive(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    headline: str = Field(min_length=1, max_length=80)
    entries: list[Entry] = Field(min_length=3, max_length=5)
    order: Literal["ascending", "countdown"] = "ascending"
    layout: Literal["fit", "crop"] = "fit"
    normalize_audio: bool = True
    title_font: Literal['Arial', 'Impact', 'Georgia', 'Trebuchet MS', 'Comic Sans MS', 'Segoe Print', 'Segoe Script', 'Ink Free', 'Bahnschrift', 'Verdana', 'Consolas', 'Segoe UI'] = 'Impact'
    rank_font: Literal['Arial', 'Impact', 'Georgia', 'Trebuchet MS', 'Comic Sans MS', 'Segoe Print', 'Segoe Script', 'Ink Free', 'Bahnschrift', 'Verdana', 'Consolas', 'Segoe UI'] = 'Arial'
    text_effect: Literal['outline', 'shadow', 'neon'] = 'outline'
    accent_color: str = Field(default='#ffdd45', pattern=r'^#[0-9a-fA-F]{6}$')
    title_color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    rank_color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    outline_color: str = Field(default='#101010', pattern=r'^#[0-9a-fA-F]{6}$')
    outline_width: int = Field(default=5, ge=0, le=12)
    shadow_color: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$')
    shadow_depth: int = Field(default=2, ge=0, le=10)
    animation_style: Literal['slide', 'pop'] = 'slide'
    title_size: int = Field(default=72, ge=48, le=96)
    rank_size: int = Field(default=54, ge=36, le=64)
    rank_position: int = Field(default=50, ge=30, le=65)
    animate_reveal: bool = True
    animate_intro: bool = True
    watermark: str = Field(default='', max_length=32)
    watermark_opacity: int = Field(default=40, ge=15, le=80)

    @field_validator('watermark')
    @classmethod
    def watermark_text(cls, value):
        value = value.lstrip('@')
        if value and not re.fullmatch(r'[\w.\-]{1,31}', value):
            raise ValueError('Use letras, números, ponto, hífen ou sublinhado no seu @, sem espaços')
        return '@' + value if value else ''

    @field_validator("headline")
    @classmethod
    def headline_text(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Informe a frase do topo")
        return value


# Ruído que o yt-dlp acrescenta a toda falha e que não ajuda quem colou o link.
_YTDLP_NOISE = re.compile(
    r"\s*(?:;\s*)?(?:please report this issue|confirm you are on the latest version"
    r"|filling out the appropriate issue template).*", re.IGNORECASE | re.DOTALL)


def readable_download_error(exc: Exception) -> str:
    """Mensagem do yt-dlp sem o pedido de abrir issue no GitHub."""
    text = " ".join(str(exc).split())
    text = _YTDLP_NOISE.sub("", text).strip(" .;,")
    text = re.sub(r"^ERROR:\s*", "", text).strip()
    return text[:240]


def ranking_ass(spec: TopFive, timeline: list[dict], path: Path):
    total = timeline[-1]["end"]
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,64,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,8,60,140,100,1
Style: Rank,Arial,44,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,7,60,140,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def ass_color(value):
        return f'&H00{value[5:7]}{value[3:5]}{value[1:3]}&'

    accent = ass_color(spec.accent_color)
    effect = (rf'\bord{spec.outline_width}\3c{ass_color(spec.outline_color)}'
              rf'\shad{spec.shadow_depth}\4c{ass_color(spec.shadow_color)}')
    if spec.text_effect == 'neon':
        effect += r'\blur5'
    elif spec.text_effect == 'shadow':
        effect += rf'\xshad{spec.shadow_depth}\yshad{spec.shadow_depth}'
    pop = r'\fscx70\fscy70\t(0,220,\fscx112\fscy112)\t(220,380,\fscx100\fscy100)'
    lines = [header]
    title = "\\N".join(text_literal(s) for s in textwrap.wrap(spec.headline, width=max(15, int(1800/spec.title_size))))
    intro_ms = min(550, int((timeline[0]['end']-timeline[0]['start'])*650))
    title_position = rf'\move(540,-420,540,115,0,{intro_ms})' if spec.animate_intro else r'\pos(540,115)'
    if spec.animate_intro and spec.animation_style == 'pop':
        title_position = r'\pos(540,115)\fad(100,0)' + pop
    lines.append(f"Dialogue: 1,0:00:00.00,{_ts(total)},Title,,0,0,0,,"
                 f"{{{title_position}\\fn{spec.title_font}\\fs{spec.title_size}\\c{ass_color(spec.title_color)}{effect}}}{title}\n")
    if spec.watermark:
        alpha = round(255*(1-spec.watermark_opacity/100))
        lines.append(f"Dialogue: 3,0:00:00.00,{_ts(total)},Title,,0,0,0,,"
                     f"{{\\an5\\pos(540,1640)\\fnArial\\fs44\\bord2\\shad1\\alpha&H{alpha:02X}&}}{text_literal(spec.watermark)}\n")
    revealed = set()
    for segment in timeline:
        begin = _ts(math.floor(segment["start"]*100+1e-6)/100)
        end = _ts(math.floor(segment["end"]*100+1e-6)/100)
        revealed.add(segment["rank"])
        for rank in range(1, len(spec.entries)+1):
            color = accent if rank == segment["rank"] else ass_color(spec.rank_color)
            name = text_literal(spec.entries[rank-1].name) if rank in revealed else ""
            spacing = 150
            y = round(1920*spec.rank_position/100 - ((len(spec.entries)-1)*spacing+spec.rank_size)/2 + (rank-1)*spacing)
            tags = f'\\fn{spec.rank_font}\\fs{spec.rank_size}\\c{color}{effect}'
            number_position = rf'\pos(45,{y})'
            if spec.animate_intro and segment is timeline[0]:
                delay = (rank-1)*min(55, intro_ms//10)
                number_position = rf'\move(-180,{y},45,{y},{delay},{intro_ms+delay})'
            if spec.animate_intro and spec.animation_style == 'pop' and segment is timeline[0]:
                number_position = rf'\pos(45,{y})\fad(100,0)' + pop
            lines.append(f"Dialogue: 2,{begin},{end},Rank,,0,0,0,,{{{number_position}{tags}\\fs{spec.rank_size+16}}}{rank}.\n")
            if name:
                position = rf'\pos(125,{y+12})'
                animation = ''
                if spec.animate_reveal and rank == segment['rank']:
                    reveal_ms = min(420, int((segment['end']-segment['start'])*650))
                    position = rf'\move(1150,{y+12},125,{y+12},0,{reveal_ms})'
                    animation = rf'\fad({min(180,reveal_ms)},0)'
                    if spec.animation_style == 'pop':
                        position = rf'\pos(125,{y+12})' + pop
                name = r'\N'.join(textwrap.wrap(name, width=max(18, int(1500/spec.rank_size))))
                lines.append(f"Dialogue: 2,{begin},{end},Rank,,0,0,0,,{{{position}{tags}{animation}}}{name}\n")
    path.write_text("".join(lines), encoding="utf-8")


def render_topfive(spec: TopFive, sources: list[Path], directory: Path, progress=lambda *_: None,
                   width=1080, height=1920):
    """Normalize each source, concatenate, then burn a frame-aligned ranking."""
    directory = directory.resolve()
    work = directory / "top5-work"
    clips = directory / "clips"
    work.mkdir(parents=True, exist_ok=True)
    clips.mkdir(parents=True, exist_ok=True)
    count = len(spec.entries)
    if len(sources) != count:
        raise ValueError('Cada posição precisa de um vídeo')
    order = list(range(count)) if spec.order == "ascending" else list(reversed(range(count)))
    infos = [probe(p) for p in sources]
    # Aponta todos os trechos inválidos de uma vez, para corrigir tudo numa só tentativa.
    problems = [f"Vídeo {i+1} ({entry.name}): o início ({entry.start:g}s) precisa ficar pelo menos 0,5 segundo "
                f"antes do fim do vídeo ({infos[i].duration:.1f}s)"
                for i, entry in enumerate(spec.entries) if infos[i].duration-entry.start < .5]
    if problems:
        raise ValueError("\n".join(problems))
    timeline, clock = [], 0.0
    for index in order:
        entry, info = spec.entries[index], infos[index]
        remaining = info.duration-entry.start
        # O fim nunca passa do vídeo: um trecho maior que o restante é encurtado.
        duration = min(entry.duration or MAX_CLIP, remaining, MAX_CLIP)
        frames = max(1, math.floor(duration*30 + 1e-6))
        duration = frames/30
        timeline.append({"rank":index+1,"name":entry.name,"url":entry.url,"source_start":entry.start,
                         "start":clock,"end":clock+duration,"duration":duration,"frames":frames})
        clock += duration
    if clock > 900:
        raise ValueError("O ranking excede o limite de 15 minutos do aplicativo. Reduza a duração dos trechos.")
    for n, segment in enumerate(timeline):
        index = segment["rank"]-1
        duration = segment["duration"]
        progress(f"preparando vídeo {segment['rank']} · {n+1}/{count}", .30+n*.45/count)
        # Numa nova tentativa, só recodifica o trecho cujo vídeo ou corte mudou.
        stat = sources[index].stat()
        key = {"source": str(sources[index].resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
               "start": segment["source_start"], "frames": segment["frames"], "layout": spec.layout,
               "normalize": spec.normalize_audio, "canvas": [width, height]}
        part, part_key = work/f"part-{n}.mov", work/f"part-{n}.json"
        if part.is_file() and part_key.is_file() and json.loads(part_key.read_text(encoding="utf-8")) == key:
            continue
        part_key.unlink(missing_ok=True)
        inputs = ["-ss", str(segment["source_start"]), "-i", str(sources[index].resolve())]
        if not infos[index].has_audio:
            inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        if spec.layout == "fit":
            video = (f"[0:v]setpts=PTS-STARTPTS,split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                     f"crop={width}:{height},boxblur=20:2[b];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[f];"
                     "[b][f]overlay=(W-w)/2:(H-h)/2")
        else:
            video = f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
        video += f",setsar=1,fps=30,tpad=stop_mode=clone:stop_duration=1,trim=duration={duration},format=yuv420p[v]"
        audio_input = "0:a" if infos[index].has_audio else "1:a"
        loudness = ",loudnorm=I=-16:TP=-1.5:LRA=11" if spec.normalize_audio and infos[index].has_audio else ""
        graph = video+f";[{audio_input}]asetpts=PTS-STARTPTS{loudness},aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration}[a]"
        # PCM inside MOV avoids AAC priming gaps at the joins. Only final audio is AAC.
        ffmpeg([*inputs,"-filter_complex",graph,"-map","[v]","-map","[a]","-t",str(duration),
                "-c:v","libx264","-preset","veryfast","-crf","18","-pix_fmt","yuv420p",
                "-c:a","pcm_s16le","-ar","48000","-ac","2",part.name], work)
        atomic_json(part_key, key)
    (work/"concat.txt").write_text("".join(f"file 'part-{n}.mov'\nduration {s['duration']:.9f}\n" for n,s in enumerate(timeline)),encoding="utf-8")
    ranking_ass(spec,timeline,work/"ranking.ass")
    progress("aplicando título e ranking", .80)
    temporary = clips/"top5-rendering.mp4"
    # Positions use proportions of the reference canvas, including when rendering test previews.
    overlay = "ass=ranking.ass"
    ffmpeg(["-f","concat","-safe","1","-i","concat.txt","-vf",overlay,"-t",str(clock),
            "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-r","30",
            "-c:a","aac","-b:a","192k","-ar","48000","-ac","2","-movflags","+faststart",str(temporary)], work)
    final = clips/"top5.mp4"
    actual = probe(temporary)
    if abs(actual.duration-clock) > .15 or not actual.has_audio:
        raise RuntimeError("A montagem não passou na verificação de duração e áudio")
    temporary.replace(final)
    ffmpeg(["-i",str(final),"-frames:v","1","-update","1",str(clips/"top5-cover.jpg")],work)
    description = "\n".join(f"{i+1}. {entry.name} — {entry.url}" for i,entry in enumerate(spec.entries))
    return {"kind":"top5","source_duration":clock,"source_resolution":f"{width}×{height}","provider":f"montagem Top {count}",
            "timeline":timeline,"headline":spec.headline,"order":spec.order,"clips":[{
                "index":1,"revision":0,"file":"top5.mp4","thumbnail":"top5-cover.jpg","title":spec.headline,
                "text":description,"description":description,"actual_duration":actual.duration,"source_start":0,"source_end":clock,
                "provider":f"montagem Top {count}","warnings":[]}]}


def load_spec(top5: dict) -> TopFive:
    """Configuração salva, com trechos de projetos anteriores ao limite encurtados para MAX_CLIP."""
    entries = [{**e, "duration": min(e["duration"], MAX_CLIP)} if (e.get("duration") or 0) > MAX_CLIP else e
               for e in top5.get("entries", [])]
    return TopFive.model_validate({**top5, "entries": entries})


def cached_source(directory: Path, index: int, url: str | None = None) -> Path | None:
    """Vídeo já baixado para a posição, se houver (e se for do mesmo link, quando informado)."""
    folder = directory/"source"/f"video-{index+1}"
    marker = folder/"download.json"
    try:
        cached = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = folder/str(cached.get("file", ""))
    if (url is not None and cached.get("url") != url) or not path.is_file() \
            or not path.resolve().is_relative_to(folder.resolve()) or path == folder:
        return None
    return path


def source_status(settings: dict, directory: Path) -> list[dict]:
    """O que cada posição já tem baixado, com a duração lida do arquivo local."""
    spec = load_spec(settings["top5"])
    result = []
    for i, entry in enumerate(spec.entries):
        path = cached_source(directory, i, entry.url)
        duration = None
        if path:
            try:
                duration = probe(path).duration
            except Exception:
                path = None
        result.append({"index": i, "downloaded": path is not None, "duration": duration})
    return result


def process_topfive(settings: dict, directory: Path, progress=lambda *_: None):
    start = time.time()
    spec = load_spec(settings["top5"])
    sources, failures = [], []
    for i, entry in enumerate(spec.entries):
        progress(f"baixando TikTok {i+1}/{len(spec.entries)}", i*.30/len(spec.entries))
        path = cached_source(directory, i, entry.url)
        if path is None:
            folder = directory/"source"/f"video-{i+1}"
            stale = cached_source(directory, i)
            try:
                path = download_tiktok(entry.url,folder)
            except Exception as exc:
                # Continua baixando os outros: a próxima tentativa só refaz o que falhou.
                failures.append(f"Vídeo {i+1} ({entry.name}): {exc}")
                continue
            if stale and stale != path:
                stale.unlink(missing_ok=True)
            atomic_json(folder/"download.json",{"url":entry.url,"file":path.name})
        sources.append(path)
    if failures:
        raise DownloadError("\n".join(failures))
    manifest = render_topfive(spec,sources,directory,progress)
    manifest["elapsed_seconds"] = round(time.time()-start,2)
    return manifest
