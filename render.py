"""Montagem e execução do comando ffmpeg final.

Truque importante: o ffmpeg roda com cwd = pasta de trabalho do clipe e os
arquivos auxiliares entram no filtergraph com nome relativo simples. Assim não
existe escape de `:` e `,` em caminho — origem clássica de filtergraph quebrado.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG, Config
from .probe import MediaInfo
from .reframe import CropPlan


class RenderError(RuntimeError):
    pass


@dataclass
class RenderSpec:
    source: Path
    start: float
    end: float
    workdir: Path
    out_path: Path
    crop: CropPlan
    sendcmd_name: str | None = None   # relativo a workdir
    ass_name: str | None = None       # relativo a workdir
    audio_copy: bool = False
    removed_ranges: list[tuple[float, float, str]] | None = None
    accent_times: list[float] | None = None
    encoder_used: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_filtergraph(spec: RenderSpec, info: MediaInfo, cfg: Config) -> str:
    c = spec.crop
    removal = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a,b,_ in (spec.removed_ranges or []))
    chain = ([f"select='not({removal})'", "setpts=N/FRAME_RATE/TB"] if removal else ["setpts=PTS-STARTPTS"])
    if spec.sendcmd_name:
        chain.append(f"sendcmd=f={spec.sendcmd_name}")
    chain.append(f"crop=w={c.crop_w}:h={c.crop_h}:x={c.x0}:y={c.y}")
    chain.append(
        f"scale={cfg.out_width}:{cfg.out_height}:flags=lanczos:force_original_aspect_ratio=decrease"
    )
    chain.append(f"pad={cfg.out_width}:{cfg.out_height}:(ow-iw)/2:(oh-ih)/2:black")
    if spec.accent_times:
        pulses="+".join(f"0.025*exp(-7*abs(on/{max(info.fps,1):.3f}-{t:.3f}))" for t in spec.accent_times)
        chain.append(f"zoompan=z='min(1.04,1+{pulses})':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={cfg.out_width}x{cfg.out_height}:fps={max(info.fps,1):.3f}")
    if spec.ass_name:
        chain.append(f"ass={spec.ass_name}")
    chain.append("format=yuv420p")
    if cfg.layout == "fit":
        graph = (f"[0:v]setpts=PTS-STARTPTS,split=2[bg][fg];"
                 f"[bg]scale={cfg.out_width}:{cfg.out_height}:force_original_aspect_ratio=increase,"
                 f"crop={cfg.out_width}:{cfg.out_height},boxblur=20:2[blur];"
                 f"[fg]scale={cfg.out_width}:{cfg.out_height}:force_original_aspect_ratio=decrease[front];"
                 "[blur][front]overlay=(W-w)/2:(H-h)/2[layout]")
    elif cfg.layout == "split":
        half = cfg.out_height // 2
        width = min(info.width, round(info.height * cfg.out_width / half)) // 2 * 2
        height = min(info.height, round(width * half / cfg.out_width)) // 2 * 2
        x1, x2 = round((info.width - width) * cfg.crop_x), round((info.width - width) * cfg.secondary_x)
        y = round((info.height - height) * cfg.crop_y)
        graph = (f"[0:v]setpts=PTS-STARTPTS,split=2[left][right];"
                 f"[left]crop={width}:{height}:{x1}:{y},scale={cfg.out_width}:{half}[top];"
                 f"[right]crop={width}:{height}:{x2}:{y},scale={cfg.out_width}:{half}[bottom];"
                 "[top][bottom]vstack[layout]")
    else:
        graph = f"[0:v]{','.join(chain)}[vout]"
    if cfg.layout in {"fit", "split"}:
        tail = (f"ass={spec.ass_name}," if spec.ass_name else "") + "format=yuv420p"
        graph += f";[layout]{tail}[vout]"
    if info.has_audio and not spec.audio_copy:
        filters = ([f"aselect='not({removal})'", "asetpts=N/SR/TB"] if removal else ["asetpts=PTS-STARTPTS"])
        filters += ["aresample=async=1:first_pts=0"]
        if cfg.denoise_audio:
            filters += ["highpass=f=80", "afftdn=nf=-25"]
        if cfg.normalize_audio:
            filters += ["loudnorm=I=-16:TP=-1.5:LRA=11", "alimiter=limit=0.89:level=false"]
        graph += f";[0:a]{','.join(filters)}[aout]"
    return graph


def render(spec: RenderSpec, info: MediaInfo, cfg: Config = CONFIG) -> Path:
    spec.workdir.mkdir(parents=True, exist_ok=True)
    spec.out_path.parent.mkdir(parents=True, exist_ok=True)

    graph_file = spec.workdir / "filtergraph.txt"
    graph_file.write_text(build_filtergraph(spec, info, cfg), encoding="utf-8")

    prefix = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{spec.start:.3f}",
        "-t", f"{spec.duration:.3f}",
        "-i", str(spec.source.resolve()),
        "-filter_complex_script", graph_file.name,
        "-map", "[vout]",
    ]
    if info.has_audio and spec.audio_copy:
        prefix += ["-map", "0:a?", "-c:a", "copy"]
    elif info.has_audio:
        prefix += ["-map", "[aout]", "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", "48000"]
    else:
        prefix += ["-an"]
    encoder = select_video_encoder(cfg.video_encoder)
    def encoding_args(name: str) -> list[str]:
        if name == "h264_nvenc": return ["-c:v", name, "-preset", "p5", "-cq", str(cfg.crf), "-b:v", "0"]
        if name == "h264_qsv": return ["-c:v", name, "-preset", "medium", "-global_quality", str(cfg.crf)]
        if name == "h264_amf": return ["-c:v", name, "-quality", "quality", "-qp_i", str(cfg.crf), "-qp_p", str(cfg.crf)]
        return ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-profile:v", "high", "-level", "4.2"]
    tail = ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if cfg.threads: tail += ["-threads", str(cfg.threads)]
    tail += [str(spec.out_path.resolve())]
    proc = subprocess.run(prefix + encoding_args(encoder) + tail, cwd=spec.workdir, capture_output=True, text=True)
    if proc.returncode != 0 and encoder != "libx264":
        encoder = "libx264"
        proc = subprocess.run(prefix + encoding_args("libx264") + tail, cwd=spec.workdir, capture_output=True, text=True)
    spec.encoder_used = encoder
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg falhou:\n{proc.stderr[-2500:]}")
    if not spec.out_path.exists() or spec.out_path.stat().st_size == 0:
        raise RenderError(f"saída vazia: {spec.out_path}")
    return spec.out_path


@lru_cache(maxsize=1)
def available_video_encoders() -> set[str]:
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    except OSError:
        return {"libx264"}
    return {name for name in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264") if name in proc.stdout}


def select_video_encoder(preference: str = "auto") -> str:
    encoders = available_video_encoders()
    requested = {"nvenc":"h264_nvenc", "qsv":"h264_qsv", "amf":"h264_amf", "cpu":"libx264"}.get(preference)
    if requested:
        return requested if requested in encoders else "libx264"
    for name in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if name in encoders: return name
    return "libx264"


def make_thumbnail(video: Path, out_path: Path, at: float = 0.5) -> Path | None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{at:.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=360:-2", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return out_path if proc.returncode == 0 and out_path.exists() else None


def ensure_ffmpeg() -> None:
    for bin_ in ("ffmpeg", "ffprobe"):
        if shutil.which(bin_) is None:
            raise RenderError(f"{bin_} não encontrado no PATH")
