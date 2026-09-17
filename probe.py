"""Metadados de mídia via ffprobe."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    rotation: int = 0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def is_vertical(self) -> bool:
        return self.aspect < 1.0


def _parse_fps(rate: str) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    return float(rate)


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.exists():
        raise ProbeError(f"arquivo não encontrado: {path}")

    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise ProbeError(f"ffprobe falhou em {path}: {out.stderr.strip()}")

    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError(f"nenhuma trilha de vídeo em {path}")

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise ProbeError(f"duração inválida em {path}")

    width = int(video.get("width", 0))
    height = int(video.get("height", 0))

    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"]) % 360
    if rotation in (90, 270):
        width, height = height, width

    fps = _parse_fps(video.get("avg_frame_rate") or "") or _parse_fps(video.get("r_frame_rate") or "") or 30.0

    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=audio is not None,
        rotation=rotation,
    )
