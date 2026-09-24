"""Editing, publication drafts and user-supplied performance snapshots."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from . import db
from cutclips.config import STORAGE
from cutclips.transcribe import Transcript

router = APIRouter(prefix="/api")
JOBS = Path(STORAGE) / "jobs"


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise HTTPException(404, "projeto não encontrado")
    base = JOBS.resolve()
    path = (base / job_id).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(404, "projeto não encontrado")
    return path


def get_clip(job_id: str, index: int) -> tuple[dict, dict]:
    job_dir(job_id)
    job = db.get_job(job_id)
    if not job or job["status"] != "done" or not job.get("manifest"):
        raise HTTPException(404, "projeto concluído não encontrado")
    clip = next((c for c in job["manifest"]["clips"] if c["index"] == index), None)
    if not clip:
        raise HTTPException(404, "clipe não encontrado")
    return job, clip


def source_path(job_id: str, job: dict) -> Path:
    base = (job_dir(job_id) / "source").resolve()
    path = (base / job["filename"]).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "vídeo original não encontrado")
    return path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditSettings(StrictModel):
    layout: Literal["track", "active", "manual", "fit", "split"] = "track"
    crop_x: FiniteFloat = Field(0.5, ge=0, le=1)
    crop_y: FiniteFloat = Field(0.5, ge=0, le=1)
    secondary_x: FiniteFloat = Field(0.8, ge=0, le=1)
    caption_size: int = Field(78, ge=24, le=120)
    caption_max_words: int = Field(4, ge=1, le=8)
    caption_position: Literal["bottom", "middle", "top"] = "bottom"
    caption_style: Literal["karaoke", "plain"] = "karaoke"
    captions_enabled: bool = True
    normalize_audio: bool = True
    denoise_audio: bool = False
    auto_edit: bool = True


class WordEdit(StrictModel):
    id: int = Field(ge=0)
    text: str = Field(max_length=100)

    @field_validator("text")
    @classmethod
    def single_line(cls, text):
        return " ".join(text.split())


class ClipEdit(StrictModel):
    revision: int = Field(ge=0)
    start: FiniteFloat = Field(ge=0)
    end: FiniteFloat = Field(gt=0)
    settings: EditSettings = Field(default_factory=EditSettings)
    words: list[WordEdit] = Field(default_factory=list, max_length=2000)


class Publication(StrictModel):
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    product: str = Field(default="", max_length=80)
    status: Literal["draft", "approved", "published"] = "draft"
    youtube_url: str = Field(default="", max_length=500)
    scheduled_date: date | None = None
    notes: str = Field(default="", max_length=2000)

    @field_validator("product")
    @classmethod
    def single_line_product(cls, text):
        """Produto marcado na vitrine (TikTok Shop): uma linha só, sem quebras."""
        return " ".join(text.split())
    thumbnail: str | None = None

    @field_validator("youtube_url")
    @classmethod
    def youtube(cls, value):
        if value:
            url = urlparse(value)
            if url.scheme != "https" or url.hostname not in {"youtube.com", "www.youtube.com", "youtu.be"} or url.username or url.password:
                raise ValueError("informe um link HTTPS do YouTube")
        return value


class Metric(StrictModel):
    date: date
    revision: int = Field(ge=0)
    views: int = Field(ge=0)
    engaged_views: int | None = Field(default=None, ge=0)
    average_percentage: FiniteFloat | None = Field(default=None, ge=0, le=1000)
    subscribers: int = Field(default=0, ge=0)
    revenue_brl: FiniteFloat | None = Field(default=None, ge=0)
    production_minutes: FiniteFloat | None = Field(default=None, ge=0)

    @field_validator("date")
    @classmethod
    def not_future(cls, value):
        if value > date.today():
            raise ValueError("a data das métricas não pode estar no futuro")
        return value


@router.get("/jobs/{job_id}/source")
def source(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "projeto não encontrado")
    return FileResponse(source_path(job_id, job))


@router.get("/jobs/{job_id}/editor/{index}")
def editor(job_id: str, index: int):
    job, clip = get_clip(job_id, index)
    source_path(job_id, job)
    path = job_dir(job_id) / "transcript.json"
    if not path.is_file():
        raise HTTPException(404, "transcrição não encontrada")
    tr = Transcript.from_json(path)
    edits = clip.get("word_edits", {})
    return {"clip": clip, "duration": job["manifest"]["source_duration"],
            "source_resolution": job["manifest"].get("source_resolution"),
            "words": [{"id": i, "start": w.start, "end": w.end,
                       "text": edits.get(str(i), w.text), "prob": w.prob} for i, w in enumerate(tr.words)],
            "publication": db.publications(job_id).get(str(index)),
            "versions": db.versions(job_id, index)}


@router.post("/jobs/{job_id}/editor/{index}", status_code=202)
def save_edit(job_id: str, index: int, data: ClipEdit):
    job, clip = get_clip(job_id, index)
    if not 0.5 <= data.end - data.start <= 180 or data.end > job["manifest"]["source_duration"]:
        raise HTTPException(422, "selecione de 0,5 a 180 segundos dentro do vídeo original")
    tr = Transcript.from_json(job_dir(job_id) / "transcript.json")
    ids = [w.id for w in data.words]
    if any(i >= len(tr.words) for i in ids) or len(set(ids)) != len(ids):
        raise HTTPException(422, "identificadores de palavras inválidos ou repetidos")
    try:
        task = db.queue_edit(job_id, index, data.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task, "status": "queued"}


@router.put("/jobs/{job_id}/publication/{index}")
def publication(job_id: str, index: int, data: Publication):
    _, clip = get_clip(job_id, index)
    if data.revision != clip.get("revision", 0):
        raise HTTPException(409, "o clipe mudou; recarregue os dados")
    allowed = set(clip.get("thumbnails", [])) | {clip.get("thumbnail")}
    if data.thumbnail not in allowed and data.thumbnail is not None:
        raise HTTPException(422, "capa não pertence a este clipe")
    if data.status == "published" and not data.youtube_url:
        raise HTTPException(422, "informe o link do vídeo publicado")
    try:
        db.save_publication(job_id, index, data.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"saved": True}


def metric_data(job_id: str, index: int, data: Metric) -> dict:
    job, clip = get_clip(job_id, index)
    if data.revision != clip.get("revision", 0):
        archived = next((v["clip"] for v in db.versions(job_id, index) if v["clip"].get("revision", 0) == data.revision), None)
        if not archived:
            raise HTTPException(409, "versão do clipe não encontrada")
        clip = archived
    return {**data.model_dump(mode="json"), "clip_title": clip["title"],
            "niche": job["manifest"].get("niche", "")}


@router.put("/jobs/{job_id}/metrics/{index}")
def metrics(job_id: str, index: int, data: Metric):
    db.save_metrics(job_id, index, metric_data(job_id, index, data))
    return {"saved": True}


@router.get("/analytics")
def analytics():
    latest = {}
    for row in db.metric_rows():
        key = (row["job_id"], row["clip_index"], row["revision"])
        if key not in latest:
            data = json.loads(row["data"])
            latest[key] = {**data, "job_id": row["job_id"], "clip_index": row["clip_index"],
                           "project_title": row["project_title"],
                           "subscribers_per_1000": round(1000 * data["subscribers"] / data["views"], 2) if data["views"] else None}
    rows = sorted(latest.values(), key=lambda r: r["views"], reverse=True)
    revenue = [r["revenue_brl"] for r in rows if r.get("revenue_brl") is not None]
    minutes = [r["production_minutes"] for r in rows if r.get("production_minutes") is not None]
    return {"clips": rows, "totals": {"views": sum(r["views"] for r in rows),
            "subscribers": sum(r["subscribers"] for r in rows),
            "revenue_brl": round(sum(revenue), 2) if revenue else None,
            "production_minutes": round(sum(minutes), 1) if minutes else None},
            "note": "Último registro acumulado por versão do clipe; valores informados por você, sem sincronização automática."}


@router.get("/analytics/template")
def metrics_template():
    return Response("job_id,clip_index,date,revision,views,engaged_views,average_percentage,subscribers,revenue_brl,production_minutes\n",
                    media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="metricas-modelo.csv"'})


@router.post("/analytics/import")
async def import_metrics(file: UploadFile = File(...)):
    raw = await file.read(2_000_001)
    await file.close()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "CSV deve ter até 2 MB")
    try:
        records = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        if not records or len(records) > 1000:
            raise ValueError("use de 1 a 1000 linhas")
        pending = []
        for line, row in enumerate(records, 2):
            job_id, index = row.pop("job_id"), int(row.pop("clip_index"))
            metric = Metric(**{k: v for k, v in row.items() if v != ""})
            pending.append((job_id, index, metric_data(job_id, index, metric)))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"CSV inválido: confira a linha {locals().get('line', 1)} e o modelo de colunas") from exc
    # Validate the whole file before committing any row.
    with db.connect() as c:
        for job_id, index, data in pending:
            c.execute("INSERT INTO metrics VALUES(?,?,?,?,?) ON CONFLICT(job_id,clip_index,date,revision) DO UPDATE SET data=excluded.data",
                      (job_id, index, data["date"], data["revision"], json.dumps(data)))
    return {"imported": len(pending)}
