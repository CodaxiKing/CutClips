from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from api import db
from api.studio import job_dir
from clipforge import topfive as t5
from clipforge.download import DownloadError
from clipforge.topfive import Entry, TopFive

router = APIRouter(prefix="/api/top5", tags=["Top 5"])


class ProbeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=600)


@router.post("", status_code=202)
def create_topfive(data: TopFive):
    for entry in data.entries:
        for asset in (entry.audio_asset if entry.audio_mode == 'replace' else None, entry.narration_asset):
            if asset:
                try:
                    t5.audio_path(asset)
                except ValueError as exc:
                    raise HTTPException(422, str(exc))
    job_id = db.create_job("top5", title=data.headline, settings={"kind":"top5", "top5":data.model_dump()})
    return {"job_id":job_id,"status":"queued"}


def _topfive_job(job_id: str) -> tuple[dict, Path]:
    directory = job_dir(job_id)
    job = db.get_job(job_id)
    if not job or (job.get("settings") or {}).get("kind") != "top5":
        raise HTTPException(404, "projeto não encontrado")
    return job, directory


@router.get("/{job_id}/sources")
def topfive_sources(job_id: str):
    """Quais vídeos já estão baixados, para a tela de erro mostrar o que será reaproveitado."""
    job, directory = _topfive_job(job_id)
    return {"sources": t5.source_status(job["settings"], directory), "max_clip": t5.MAX_CLIP}


@router.put("/{job_id}/entries/{index}")
def update_topfive_entry(job_id: str, index: int, entry: Entry):
    """Corrige uma posição de um ranking com erro e o recoloca na fila; o resto é reaproveitado."""
    job, _ = _topfive_job(job_id)
    if job["status"] != "error":
        raise HTTPException(409, "só dá para editar uma posição quando o projeto está com erro")
    top5 = dict(job["settings"]["top5"])
    if not 0 <= index < len(top5["entries"]):
        raise HTTPException(404, "posição não encontrada")
    top5["entries"] = [{**e, **entry.model_dump(exclude_unset=True)} if i == index else e for i, e in enumerate(top5["entries"])]
    try:
        spec = t5.load_spec(top5)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(e["msg"] for e in exc.errors())) from exc
    db.update_settings(job_id, {**job["settings"], "top5": spec.model_dump()})
    if not db.retry(job_id):
        raise HTTPException(409, "o projeto mudou de estado; recarregue a página")
    return {"job_id": job_id, "status": "queued"}


@router.post("/probe")
def probe_topfive_source(data: ProbeRequest):
    """Duração do vídeo para o formulário propor um trecho que caiba nele."""
    try:
        url = Entry.tiktok_url(data.url.strip())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        duration = t5.source_duration(url)
    except DownloadError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"duration": duration, "max_clip": t5.MAX_CLIP}
