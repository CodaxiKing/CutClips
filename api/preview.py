"""Trecho leve de um link, só para a prévia da interface tocar o vídeo de verdade.

Baixa uma janela curta (não o vídeo inteiro) em resolução baixa e guarda no
storage. A montagem final continua baixando a fonte completa pelo worker; aqui o
objetivo é ver o enquadramento antes de gerar.
"""
from __future__ import annotations

import hashlib
import tempfile
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from cutclips.config import STORAGE, Config
from cutclips.download import DownloadError, is_supported_url
from cutclips.montage import download_tiktok, ffmpeg, is_tiktok_url
from pathlib import Path

router = APIRouter(prefix="/api/preview", tags=["Prévia"])

WINDOW = 30.0        # segundos baixados a partir do início escolhido
MAX_SOURCE = 3 * 3600  # fonte mais longa que isto não vira prévia
KEPT = 60            # prévias guardadas; as mais antigas somem
_locks: dict[str, threading.Lock] = {}
_lock = threading.Lock()


def cache_key(url: str, start: float) -> str:
    return hashlib.sha256(f"{url}|{start:.1f}".encode()).hexdigest()[:32]


def cut_window(source: Path, start: float, target: Path) -> Path:
    """Corta a janela de um arquivo já baixado, reduzindo para caber na prévia."""
    ffmpeg(["-ss", f"{start:.3f}", "-i", str(source.resolve()), "-t", f"{WINDOW:.3f}",
            "-vf", "scale=-2:'min(480,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(target.resolve())], target.parent)
    if not target.is_file():
        raise DownloadError("a prévia não gerou arquivo")
    return target


def download_window(url: str, start: float, target: Path) -> Path:
    """Janela de WINDOW segundos a partir de `start`, em até 480p com áudio."""
    import yt_dlp
    from yt_dlp.utils import download_range_func
    from cutclips.download import _base_opts

    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    if is_tiktok_url(url):
        # O TikTok costuma recusar o primeiro pedido: reaproveitamos o download com repetição.
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            return cut_window(download_tiktok(url, Path(temporary)), start, target)
    opts = _base_opts(Config(), folder, lambda _: None)
    opts.update({
        "format": "bv*[height<=480][vcodec^=avc1]+ba[ext=m4a]/b[height<=480][ext=mp4]/bv*[height<=480]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(target.with_suffix("").with_name(target.stem + ".%(ext)s")),
        "download_ranges": download_range_func(None, [(start, start + WINDOW)]),
        "force_keyframes_at_cuts": True,
        "socket_timeout": 25,
        "retries": 2,
        "match_filter": lambda info, *, incomplete=False:
            "vídeo longo demais para a prévia" if (info.get("duration") or 0) > MAX_SOURCE else None,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    produced = [p for p in folder.glob(target.stem + ".*") if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not produced:
        raise DownloadError("a prévia não gerou arquivo")
    path = produced[0]
    if path != target:
        path.replace(target)
    for old in sorted(folder.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)[KEPT:]:
        old.unlink(missing_ok=True)
    return target


@router.get("/source")
def preview_source(url: str = Query(min_length=1, max_length=600), start: float = Query(0, ge=0, le=3600)):
    url = url.strip()
    if not (is_tiktok_url(url) or is_supported_url(url)):
        raise HTTPException(422, "Use um link de vídeo do TikTok, YouTube, Twitch ou Kick")
    folder = Path(STORAGE) / "preview-cache"
    target = folder / f"{cache_key(url, start)}.mp4"
    with _lock:
        guard = _locks.setdefault(target.name, threading.Lock())
    with guard:
        if not target.is_file():
            try:
                download_window(url, start, target)
            except Exception as exc:
                raise HTTPException(502, "Não foi possível preparar a prévia deste link") from exc
    return FileResponse(target, media_type="video/mp4")
