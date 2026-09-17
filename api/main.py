"""API HTTP. Só recebe arquivo, enfileira e serve resultado — nada pesado aqui.

O processamento vive no worker justamente para que um upload de 4 GB não
segure o event loop nem derrube a interface.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import re
import shutil
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import db  # noqa: E402
from clipforge.config import STORAGE, Config, ORIENTATIONS, apply_orientation  # noqa: E402
from clipforge.download import is_supported_url, platform_of  # noqa: E402
from api.studio import router, job_dir  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"
JOBS = Path(STORAGE) / "jobs"
ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
CHUNK = 1 << 22  # 4 MB
MAX_UPLOAD_BYTES = int(os.getenv("CLIPFORGE_MAX_UPLOAD_BYTES", str(4 * 1024**3)))

@asynccontextmanager
async def lifespan(app):
    db.init()
    JOBS.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="ClipForge", version="0.2.0", lifespan=lifespan)
app.include_router(router)
from api.channel import router as channel_router
app.include_router(channel_router)
from api.youtube import router as youtube_router
app.include_router(youtube_router)
from api.topfive import router as topfive_router
app.include_router(topfive_router)
from api.topfive_tools import router as topfive_tools_router
app.include_router(topfive_tools_router)
from api.preview import router as preview_router
app.include_router(preview_router)
from api.trending import router as trending_router
app.include_router(trending_router)
from api.montages import router as montages_router
app.include_router(montages_router)


def _safe_name(name: str) -> str:
    name = re.split(r"[/\\]", name)[-1]
    name = re.sub(r"[^\w.\- ]", "_", name).strip() or "video.mp4"
    suffix = Path(name).suffix
    stem = Path(name).stem[:100].strip(" .") or "video"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", stem):
        stem = "video-" + stem
    return stem + suffix


def _clip_path(job_id: str, filename: str) -> Path:
    """Resolve dentro da pasta do job. Bloqueia travessia de caminho."""
    base = (job_dir(job_id) / "clips").resolve()
    if "/" in filename or "\\" in filename:
        raise HTTPException(404, "arquivo não encontrado")
    target = (base / filename).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(404, "arquivo não encontrado")
    return target


# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = WEB / "index.html"
    if not page.exists():
        return "<h1>ClipForge</h1><p>interface não encontrada</p>"
    return page.read_text(encoding="utf-8")


@app.get("/assets/{filename}")
def asset(filename: str):
    if filename not in {"studio.js", "studio.css", "channel.js", "channel.css", "topfive.js", "topfive.css",
                        "montages.js", "montages.css", "theme.css", "discover.js", "discover.css", "topfive-tools.js", "topfive-tools.css", "narration.js"}:
        raise HTTPException(404)
    return FileResponse(WEB / filename)


@app.get("/api/health")
def health() -> dict:
    jobs = db.list_jobs(limit=200)
    return {
        "ok": True,
        "queued": sum(1 for j in jobs if j["status"] == "queued"),
        "running": sum(1 for j in jobs if j["status"] == "running"),
    }


@app.post("/api/jobs")
async def create_job(
    file: UploadFile | None = File(None),
    url: str = Form(""),
    title: str = Form("", max_length=200),
    max_clips: int = Form(10),
    min_duration: float = Form(20.0),
    max_duration: float = Form(90.0),
    llm_provider: str = Form("anthropic"),
    llm_model: str = Form("", max_length=100),
    triage_model: str = Form("", max_length=100),
    review_model: str = Form("", max_length=100),
    whisper_model: str = Form("", max_length=100),
    language: str = Form("", max_length=12),
    caption_size: int = Form(78),
    niche: str = Form("", max_length=200),
    audience: str = Form("", max_length=300),
    rights_notes: str = Form("", max_length=2000),
    layout: str = Form("track"),
    orientation: str = Form("vertical"),
    live_minutes: float = Form(0.0),
    normalize_audio: bool = Form(True),
    denoise_audio: bool = Form(False),
    auto_edit: bool = Form(True),
) -> dict:
    url = url.strip()
    has_file = file is not None and bool(file.filename)
    if has_file == bool(url):
        raise HTTPException(400, "envie um arquivo ou um link (um dos dois)")
    if url and not is_supported_url(url):
        raise HTTPException(400, "links aceitos: YouTube, Twitch e Kick")
    if orientation not in ORIENTATIONS:
        raise HTTPException(422, "orientação inválida")
    if live_minutes and not url:
        raise HTTPException(400, "gravar transmissão exige o link da live")

    settings = {
        "max_clips": max_clips,
        "min_duration": min_duration,
        "max_duration": max_duration,
        "llm_provider": llm_provider,
        "caption_size": caption_size,
        "niche": niche, "audience": audience, "rights_notes": rights_notes,
        "layout": layout, "normalize_audio": normalize_audio, "denoise_audio": denoise_audio,
        "auto_edit": auto_edit, "orientation": orientation,
    }
    if live_minutes:
        settings["live_minutes"] = live_minutes
    try:
        apply_orientation(Config(**settings), orientation).validate()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for key, value in [("llm_model", llm_model), ("triage_model",triage_model), ("review_model",review_model), ("whisper_model", whisper_model),
                       ("language", language)]:
        if value:
            settings[key] = value

    if url:
        # o worker baixa; até lá o "filename" mostrado é a própria URL
        job_id = db.create_job(url, title=title, settings=settings, source_url=url)
        return {"job_id": job_id, "url": url, "status": "queued",
                "platform": platform_of(url)}

    name = _safe_name(file.filename or "video.mp4")
    if Path(name).suffix.lower() not in ALLOWED_EXT:
        raise HTTPException(400, f"extensão não suportada: {Path(name).suffix}")

    job_id = db.create_job(name, title=title[:200], settings=settings, status="uploading")
    dest_dir = JOBS / job_id / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    partial = dest_dir / (name + ".part")

    size = 0
    try:
        with partial.open("wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "arquivo excede o limite de upload")
                await run_in_threadpool(out.write, chunk)
        if size == 0:
            raise HTTPException(400, "arquivo vazio")
        partial.replace(dest)
        db.enqueue_upload(job_id)
    except BaseException as exc:
        partial.unlink(missing_ok=True)
        db.fail(job_id, "upload interrompido ou inválido")
        if isinstance(exc, HTTPException) or not isinstance(exc, Exception):
            raise
        raise HTTPException(500, "upload falhou") from exc
    finally:
        await file.close()

    return {"job_id": job_id, "filename": name, "bytes": size, "status": "queued"}


@app.get("/api/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=200)) -> dict:
    return {"jobs": db.list_jobs(limit=min(limit, 200))}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job não encontrado")
    job["edits"] = db.edit_status(job_id)
    job["publication"] = db.publications(job_id)
    return job


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    directory = job_dir(job_id)
    try:
        deleted = db.delete_job(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "job não encontrado")
    shutil.rmtree(directory, ignore_errors=True)
    return {"deleted": job_id}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    job_dir(job_id)
    if not db.retry(job_id):
        raise HTTPException(409, "somente projetos com erro podem ser reenfileirados")
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}/clips/{filename}")
def get_clip(job_id: str, filename: str):
    path = _clip_path(job_id, filename)
    media = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".srt": "application/x-subrip"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


@app.get("/api/jobs/{job_id}/download")
def download_all(job_id: str, approved_only: bool = False, clip_index: int | None = None):
    job = db.get_job(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "job não concluído")
    publications = db.publications(job_id)
    clips = [c for c in job["manifest"]["clips"] if (clip_index is None or c["index"] == clip_index)
             and (not approved_only or publications.get(str(c["index"]), {}).get("status") in {"approved", "published"})]
    if not clips:
        raise HTTPException(404, "nenhum clipe corresponde ao filtro")
    handle, filename = tempfile.mkstemp(suffix=".zip")
    os.close(handle)
    archive = Path(filename)
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
            for clip in clips:
                pub = publications.get(str(clip["index"]), {})
                for name in (clip["file"], clip.get("subtitle"), pub.get("thumbnail") or clip.get("thumbnail")):
                    if name:
                        path = _clip_path(job_id, name)
                        zf.write(path, path.name)
                text = (pub.get("title") or clip["title"]) + "\n\n" + (pub.get("description") or clip.get("description") or clip["text"])
                zf.writestr(f"{clip['index']:02d}-publicacao.txt", text)
            zf.writestr("manifest.json", json.dumps({**job["manifest"], "clips": clips, "publication": publications}, ensure_ascii=False, indent=2))
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    stem = re.sub(r"\W+", "-", (job["title"] or job["filename"]))[:40].strip("-") or "clipes"
    return FileResponse(archive, media_type="application/zip", filename=f"{stem}-{job_id}.zip",
                        background=BackgroundTask(archive.unlink, missing_ok=True))
