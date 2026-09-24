"""Motion Control through a local Wan Animate ComfyUI workflow."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from api.local_comfy import (WAN_FPS, WAN_MAX_FRAMES, load_local_template, local_video_workflow,
                              wan_animate_workflow, wan_models, wan_size)
from cutclips.config import STORAGE
from cutclips.probe import ProbeError, probe

router = APIRouter(prefix="/api/motion-control", tags=["Motion Control"])
ROOT = Path(STORAGE) / "motion-control"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".webm", ".m4v"}
MAX_IMAGE = 15 * 1024 * 1024
MAX_VIDEO = 150 * 1024 * 1024
MAX_RESULT = 600 * 1024 * 1024
_lock = threading.Lock()


def _base_url() -> str:
    url = os.getenv("CUTCLIPS_COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("CUTCLIPS_COMFYUI_URL deve apontar para um ComfyUI local (localhost).")
    return url


def _record(job_id: str) -> dict:
    path = ROOT / job_id / "status.json"
    if not path.is_file():
        raise HTTPException(404, "Geração não encontrada")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(job_id: str, **changes) -> dict:
    with _lock:
        path = ROOT / job_id / "status.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


async def _copy_upload(upload: UploadFile, target: Path, limit: int) -> None:
    size = 0
    with target.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                target.unlink(missing_ok=True)
                raise HTTPException(413, "Arquivo maior que o limite permitido")
            out.write(chunk)
    if not size:
        target.unlink(missing_ok=True)
        raise HTTPException(422, "O arquivo está vazio")


def _upload(client: httpx.Client, path: Path) -> str:
    # ComfyUI uses /upload/image for arbitrary files in its input directory,
    # including videos consumed by LoadVideo.
    with path.open("rb") as file:
        response = client.post("/upload/image", files={"image": (path.name, file)}, data={"type": "input"})
    response.raise_for_status()
    return response.json()["name"]


def _workflow(image: str, video: str, prompt: str) -> dict:
    return local_video_workflow(image, video, prompt)[0]


def _custom_template() -> bool:
    """CUTCLIPS_WAN_WORKFLOW substitui o fluxo embutido quando configurado."""
    return bool(os.getenv("CUTCLIPS_WAN_WORKFLOW", "").strip())


# Nós e modelos exigidos pelo fluxo embutido (ComfyUI-GGUF e comfyui_controlnet_aux).
BUILTIN_LOADERS = (("UnetLoaderGGUF", "unet_name", "unet"), ("LoraLoaderModelOnly", "lora_name", "lora"),
                   ("CLIPLoader", "clip_name", "text_encoder"), ("CLIPVisionLoader", "clip_name", "clip_vision"),
                   ("VAELoader", "vae_name", "vae"))
BUILTIN_NODES = ("DWPreprocessor", "WanAnimateToVideo", "GetVideoComponents", "CreateVideo", "SaveVideo")


def _builtin_missing(info: dict) -> list[str]:
    models = wan_models()
    missing = [node for node in BUILTIN_NODES if node not in info]
    for node, field, key in BUILTIN_LOADERS:
        try:
            if models[key] not in info[node]["input"]["required"][field][0]:
                missing.append(models[key])
        except (KeyError, IndexError, TypeError):
            missing.append(node)
    return missing


def _prepare_motion(source: Path, target: Path) -> int:
    """Converte o vídeo de movimento para o ritmo do Wan e corta no limite de uma passada."""
    seconds = WAN_MAX_FRAMES / WAN_FPS
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-t", f"{seconds:.3f}",
                    "-vf", f"fps={WAN_FPS}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                    "-c:a", "aac", str(target)], check=True, capture_output=True)
    return min(WAN_MAX_FRAMES, round(probe(target).duration * WAN_FPS))


def _add_audio(video: Path, motion: Path) -> None:
    """Devolve ao resultado o áudio do vídeo de movimento, quando ele existe."""
    if not probe(motion).has_audio:
        return
    mixed = video.with_name("result-audio.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-i", str(motion),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(mixed)],
                   check=True, capture_output=True)
    mixed.replace(video)


def _run(job_id: str, prompt: str) -> None:
    folder = ROOT / job_id
    try:
        with httpx.Client(base_url=_base_url(), timeout=httpx.Timeout(60, read=180)) as client:
            info = client.get("/object_info")
            info.raise_for_status()
            if not _custom_template() and (missing := _builtin_missing(info.json())):
                raise RuntimeError("Wan Animate incompleto no ComfyUI. Faltando: " + ", ".join(missing))
            _save(job_id, status="uploading")
            source = next(path for path in folder.glob("motion.*") if path.stem == "motion")
            prepared = folder / "motion-16fps.mp4"
            frames = _prepare_motion(source, prepared)
            image = _upload(client, next(folder.glob("person.*")))
            video = _upload(client, prepared)
            if _custom_template():
                workflow, output_id = local_video_workflow(image, video, prompt)
            else:
                media = probe(prepared)
                width, height = wan_size(media.width, media.height)
                workflow, output_id = wan_animate_workflow(image, video, prompt, width, height, frames)
            response = client.post("/prompt", json={"prompt": workflow, "client_id": job_id})
            if response.is_error:
                raise RuntimeError(f"ComfyUI recusou o fluxo ({response.status_code}): {response.text[:500]}")
            body = response.json()
            if "error" in body:
                raise RuntimeError(str(body["error"]))
            prompt_id = body["prompt_id"]
            _save(job_id, status="processing", prompt_id=prompt_id)
            deadline = time.monotonic() + 60 * 90
            while time.monotonic() < deadline:
                time.sleep(5)
                response = client.get(f"/history/{prompt_id}")
                response.raise_for_status()
                item = response.json().get(prompt_id)
                if not item:
                    continue
                if item.get("status", {}).get("status_str") == "error":
                    messages = item.get("status", {}).get("messages", [])
                    raise RuntimeError(str(messages[-1] if messages else "Falha no ComfyUI"))
                outputs = item.get("outputs", {}).get(output_id, {})
                videos = outputs.get("videos") or outputs.get("gifs") or []
                if videos:
                    entry = videos[0]
                    result = folder / "result.mp4"
                    with client.stream("GET", "/view", params={
                        "filename": entry["filename"], "subfolder": entry.get("subfolder", ""),
                        "type": entry.get("type", "output"),
                    }) as download:
                        download.raise_for_status()
                        size = 0
                        with result.open("wb") as out:
                            for chunk in download.iter_bytes(1024 * 1024):
                                size += len(chunk)
                                if size > MAX_RESULT:
                                    raise RuntimeError("O vídeo gerado excede 600 MB")
                                out.write(chunk)
                    _add_audio(result, prepared)
                    _save(job_id, status="done")
                    return
                if item.get("status", {}).get("completed"):
                    raise RuntimeError("O ComfyUI concluiu a tarefa sem devolver um vídeo. Verifique o nó SaveVideo.")
            raise RuntimeError("Tempo de espera excedido no ComfyUI (90 min)")
    except subprocess.CalledProcessError as exc:
        _save(job_id, status="error", error="FFmpeg falhou: " + exc.stderr.decode(errors="replace")[-500:])
    except Exception as exc:
        _save(job_id, status="error", error=str(exc)[:1000])


@router.get("/config")
def configuration():
    try:
        with httpx.Client(base_url=_base_url(), timeout=3) as client:
            response = client.get("/object_info")
            response.raise_for_status()
            if not _custom_template():
                missing = _builtin_missing(response.json())
                return {"ready": not missing, "connected": True, "url": _base_url(), "template": not missing,
                        "missing": missing}
            try:
                flow = load_local_template("CUTCLIPS_WAN_WORKFLOW")
                required = {node["class_type"] for node in flow.values()}
                return {"ready": required.issubset(response.json()), "connected": True, "url": _base_url(), "template": True}
            except ValueError:
                return {"ready": False, "connected": True, "url": _base_url(), "template": False}
    except (httpx.HTTPError, ValueError):
        return {"ready": False, "connected": False, "template": False,
                "url": os.getenv("CUTCLIPS_COMFYUI_URL", "http://127.0.0.1:8188")}


@router.post("")
async def create(
    image: UploadFile = File(...), video: UploadFile = File(...),
    prompt: str = Form("A pessoa da imagem executa os movimentos do vídeo de referência."),
):
    if Path(image.filename or "").suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "Use uma imagem PNG, JPG ou WebP")
    if Path(video.filename or "").suffix.lower() not in VIDEO_EXT:
        raise HTTPException(422, "Use um vídeo MP4, WebM ou M4V")
    if len(prompt) > 2500:
        raise HTTPException(422, "Descrição longa demais")
    if _custom_template():
        try:
            flow = load_local_template("CUTCLIPS_WAN_WORKFLOW")
            if not any(node["class_type"] == "SaveVideo" for node in flow.values()):
                raise ValueError("O fluxo Wan precisa terminar em SaveVideo")
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
    job_id = uuid.uuid4().hex
    folder = ROOT / job_id
    folder.mkdir(parents=True, exist_ok=False)
    try:
        await _copy_upload(image, folder / f"person{Path(image.filename).suffix.lower()}", MAX_IMAGE)
        video_path = folder / f"motion{Path(video.filename).suffix.lower()}"
        await _copy_upload(video, video_path, MAX_VIDEO)
        try:
            media = probe(video_path)
        except (ProbeError, OSError) as exc:
            raise HTTPException(422, "O vídeo enviado não pôde ser lido") from exc
        if not 2 <= media.duration <= 30:
            raise HTTPException(422, "O vídeo precisa ter entre 2 e 30 segundos; os primeiros ~5 s são animados")
    except Exception:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
        raise
    (folder / "status.json").write_text(json.dumps({
        "id": job_id, "status": "queued", "model": "Wan local", "created_at": time.time(),
        "image_name": Path(image.filename).name, "video_name": Path(video.filename).name,
    }, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_run, args=(job_id, prompt), daemon=True).start()
    return _record(job_id)


@router.get("")
def history():
    if not ROOT.exists():
        return {"jobs": []}
    jobs = []
    for path in ROOT.glob("*/status.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in {"queued", "uploading", "processing"} and time.time() - job["created_at"] > 3600:
                job = _save(job["id"], status="error", error="Geração interrompida. Envie novamente.")
            jobs.append(job)
        except (ValueError, KeyError, OSError):
            continue
    return {"jobs": sorted(jobs, key=lambda item: item["created_at"], reverse=True)[:50]}


@router.get("/{job_id}")
def status(job_id: str):
    if not job_id.isalnum():
        raise HTTPException(404)
    return _record(job_id)


@router.get("/{job_id}/download")
def download(job_id: str):
    if not job_id.isalnum() or not (ROOT / job_id / "result.mp4").is_file():
        raise HTTPException(404, "Vídeo ainda não disponível")
    return FileResponse(ROOT / job_id / "result.mp4", media_type="video/mp4", filename=f"motion-control-{job_id[:8]}.mp4")


@router.get("/{job_id}/view")
def view(job_id: str):
    if not job_id.isalnum() or not (ROOT / job_id / "result.mp4").is_file():
        raise HTTPException(404, "Vídeo ainda não disponível")
    return FileResponse(ROOT / job_id / "result.mp4", media_type="video/mp4")
