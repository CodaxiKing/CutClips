"""Photorealistic influencer images and rotatable GLB models via local ComfyUI."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from api.local_comfy import flux_image_workflow, local_3d_workflow, load_local_template
from cutclips.config import STORAGE
from api.motion_control import _base_url, _upload

router = APIRouter(prefix="/api/influencers", tags=["Influencers IA"])
ROOT = Path(STORAGE) / "influencers"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE = 20 * 1024 * 1024
MAX_OUTPUT = 250 * 1024 * 1024
_lock = threading.Lock()


def _folder(job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404, "Geração não encontrada")
    return ROOT / job_id


def _read(job_id: str) -> dict:
    path = _folder(job_id) / "status.json"
    if not path.is_file():
        raise HTTPException(404, "Geração não encontrada")
    return json.loads(path.read_text(encoding="utf-8"))


def _update(job_id: str, **changes) -> dict:
    with _lock:
        path = _folder(job_id) / "status.json"
        job = json.loads(path.read_text(encoding="utf-8"))
        job.update(changes)
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return job


async def _copy_image(upload: UploadFile | None, folder: Path, stem: str) -> Path | None:
    if upload is None or not upload.filename:
        return None
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in IMAGE_EXT:
        raise HTTPException(422, "Use uma imagem PNG, JPG ou WebP")
    target = folder / f"{stem}{suffix}"
    size = 0
    with target.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_IMAGE:
                target.unlink(missing_ok=True)
                raise HTTPException(413, "Imagem maior que 20 MB")
            out.write(chunk)
    if not size:
        target.unlink(missing_ok=True)
        raise HTTPException(422, "A imagem está vazia")
    try:
        with Image.open(target) as image:
            width, height = image.size
            image.verify()
        if width < 128 or height < 128:
            raise ValueError("dimensão insuficiente")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, "Imagem inválida ou menor que 128 × 128 pixels") from exc
    return target


def _reference(path: Path, megapixels: float = 1.0) -> Path:
    """Copy of a reference photo at ~1 MP: FLUX encodes references at full size and 8 GB GPUs run out."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        scale = (megapixels * 1_000_000 / (image.width * image.height)) ** 0.5
        if scale < 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
        target = path.with_name(f"{path.stem}-ref.png")
        image.save(target)
    return target


def _source_image(source_job: str) -> Path:
    job = _read(source_job)
    path = _folder(source_job) / "result.png"
    if job.get("status") != "done" or job.get("kind") != "image" or not path.is_file():
        raise HTTPException(422, "A imagem de referência ainda não está disponível")
    return path


def _prompt(mode: str, description: str, has_scene_image: bool) -> str:
    base = (
        "Create an ultra-photorealistic professional camera photograph of an adult fictional AI influencer. "
        "Render natural skin with visible pores and subtle imperfections, individual hair strands, realistic eyes and hands, "
        "accurate anatomy, believable fabric weave and folds, and physically consistent light, shadows and reflections. "
        "Use natural color grading and photographic depth of field. Avoid CGI, illustration, plastic or waxy skin, "
        "beauty-filter smoothing, exaggerated features, extra fingers, distorted anatomy, text, logos and watermarks."
    )
    if mode == "text":
        return f"{base}\nCreative direction: {description}"
    if mode == "reference":
        return f"{base}\nImage 1 is the identity reference. Preserve the same adult person's recognizable face, hair, skin tone and body proportions. Creative direction: {description}"
    if mode == "outfit":
        return (f"{base}\nImage 1 is the person. Image 2 is the clothing reference. Change only the person's outfit to the garment in image 2, matching its cut, color, texture and details. Preserve the person's face, hair, body, pose, background and lighting as closely as possible. Additional direction: {description}")
    if mode == "product":
        return (f"{base}\nImage 1 is the person. Image 2 is the product being advertised. "
                "Make the person hold the product from image 2 naturally and prominently toward the camera, "
                "like an influencer showcasing it in an ad, keeping the product's exact appearance, label, color and details. "
                "Preserve the person's face, hair, body, background and lighting as closely as possible. "
                f"Additional direction: {description}")
    place = "Image 2 is the location reference. " if has_scene_image else ""
    return f"{base}\nImage 1 is the person. {place}Place the same person in the requested location. Preserve her recognizable face, hair and body proportions, with natural perspective and lighting matching the new scene. Location and action: {description}"


def _image_workflow(mode: str, description: str, aspect: str, resolution: str, images: list[str]) -> dict:
    return flux_image_workflow(_prompt(mode, description, len(images) > 1 and mode == "scene"),
                               aspect, resolution, images)[0]


def _model_workflow(images: dict[str, str]) -> dict:
    return local_3d_workflow(images)[0]


def _download_result(client: httpx.Client, entry: dict, target: Path) -> None:
    with client.stream("GET", "/view", params={
        "filename": entry["filename"], "subfolder": entry.get("subfolder", ""),
        "type": entry.get("type", "output"),
    }) as response:
        response.raise_for_status()
        size = 0
        with target.open("wb") as out:
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > MAX_OUTPUT:
                    target.unlink(missing_ok=True)
                    raise RuntimeError("Resultado maior que 250 MB")
                out.write(chunk)


def _run(job_id: str, mode: str, description: str, aspect: str, resolution: str) -> None:
    folder = _folder(job_id)
    kind = "model" if mode == "model" else "image"
    try:
        with httpx.Client(base_url=_base_url(), timeout=httpx.Timeout(60, read=180)) as client:
            required = (["LoadImage", "SaveGLB"] if kind == "model" else
                        ["UNETLoader", "CLIPLoader", "VAELoader", "Flux2Scheduler", "ReferenceLatent", "SaveImage"])
            for node in required:
                response = client.get(f"/object_info/{node}")
                response.raise_for_status()
                if node not in response.json():
                    raise RuntimeError(f"Nó local {node} indisponível no ComfyUI. Atualize o ComfyUI.")
            _update(job_id, status="uploading")
            files = [path for stem in ("person", "secondary", "left", "right", "back") for path in folder.glob(stem + ".*")]
            uploaded = {path.stem: _upload(client, path if kind == "model" else _reference(path)) for path in files}
            names = ([] if mode == "text" else [uploaded["person"]] +
                     ([uploaded["secondary"]] if mode in {"outfit", "scene"} and "secondary" in uploaded else []))
            if kind == "model":
                flow, output_id = local_3d_workflow(uploaded)
            else:
                flow, output_id = flux_image_workflow(
                    _prompt(mode, description, len(names) > 1 and mode == "scene"), aspect, resolution, names)
            response = client.post("/prompt", json={"prompt": flow, "client_id": job_id})
            if response.is_error:
                raise RuntimeError(f"ComfyUI recusou o fluxo ({response.status_code}): {response.text[:500]}")
            body = response.json()
            if body.get("error"):
                raise RuntimeError(str(body["error"]))
            prompt_id = body["prompt_id"]
            _update(job_id, status="processing", prompt_id=prompt_id)
            deadline = time.monotonic() + 60 * 45
            while time.monotonic() < deadline:
                time.sleep(4)
                response = client.get(f"/history/{prompt_id}")
                response.raise_for_status()
                item = response.json().get(prompt_id)
                if not item:
                    continue
                if item.get("status", {}).get("status_str") == "error":
                    messages = item.get("status", {}).get("messages", [])
                    raise RuntimeError(str(messages[-1] if messages else "Falha no ComfyUI"))
                outputs = item.get("outputs", {}).get(output_id, {})
                entries = outputs.get("3d") if kind == "model" else outputs.get("images")
                if entries:
                    target = folder / ("result.glb" if kind == "model" else "result.png")
                    _download_result(client, entries[0], target)
                    _update(job_id, status="done")
                    return
                if item.get("status", {}).get("completed"):
                    raise RuntimeError("O ComfyUI concluiu sem devolver o arquivo esperado.")
            raise RuntimeError("Tempo de espera excedido no ComfyUI (45 min)")
    except Exception as exc:
        _update(job_id, status="error", error=str(exc)[:1000])


@router.get("/config")
def config():
    try:
        with httpx.Client(base_url=_base_url(), timeout=3) as client:
            response = client.get("/object_info")
            response.raise_for_status()
            info = response.json()
            try:
                load_local_template("CUTCLIPS_HUNYUAN3D_WORKFLOW")
                model_template = True
            except ValueError:
                model_template = False
            def available(node: str, field: str, name: str) -> bool:
                try:
                    choices = info[node]["input"]["required"][field][0]
                    return name in choices
                except (KeyError, IndexError, TypeError):
                    return False
            images_ready = all(node in info for node in ("UNETLoader", "Flux2Scheduler", "ReferenceLatent"))
            images_ready = images_ready and all((
                available("UNETLoader", "unet_name", os.getenv("CUTCLIPS_FLUX_MODEL", "flux-2-klein-4b-fp8.safetensors")),
                available("CLIPLoader", "clip_name", os.getenv("CUTCLIPS_FLUX_TEXT_ENCODER", "qwen_3_4b.safetensors")),
                available("VAELoader", "vae_name", os.getenv("CUTCLIPS_FLUX_VAE", "flux2-vae.safetensors")),
            ))
            return {"connected": True, "images": images_ready,
                    "models": model_template and "SaveGLB" in info}
    except (httpx.HTTPError, ValueError):
        return {"connected": False, "images": False, "models": False}


@router.post("")
async def create(
    mode: str = Form(...), description: str = Form(""), aspect: str = Form("3:4"),
    resolution: str = Form("1K"), source_job: str = Form(""),
    person: UploadFile | None = File(None), secondary: UploadFile | None = File(None),
    left: UploadFile | None = File(None), right: UploadFile | None = File(None), back: UploadFile | None = File(None),
):
    if mode not in {"text", "reference", "outfit", "scene", "model"}:
        raise HTTPException(422, "Modo inválido")
    if aspect not in {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "9:16", "16:9"} or resolution not in {"1K", "2K"}:
        raise HTTPException(422, "Formato inválido")
    description = description.strip()
    if mode != "model" and not description:
        raise HTTPException(422, "Descreva a imagem desejada")
    if len(description) > 1800:
        raise HTTPException(422, "Descrição longa demais")
    if mode == "model":
        try:
            load_local_template("CUTCLIPS_HUNYUAN3D_WORKFLOW")
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
    source = _source_image(source_job) if source_job and mode != "text" else None
    if mode != "text" and not source and not (person and person.filename):
        raise HTTPException(422, "Envie uma imagem da influencer ou escolha uma do histórico")
    if mode == "outfit" and not (secondary and secondary.filename):
        raise HTTPException(422, "Envie a imagem da roupa")
    job_id = uuid.uuid4().hex
    folder = _folder(job_id)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        person_path = await _copy_image(person, folder, "person") if mode != "text" else None
        if source and not person_path:
            (folder / "person.png").write_bytes(source.read_bytes())
        if mode in {"outfit", "scene"}:
            await _copy_image(secondary, folder, "secondary")
        if mode == "model":
            for stem, upload in (("left", left), ("right", right), ("back", back)):
                await _copy_image(upload, folder, stem)
    except Exception:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
        raise
    (folder / "status.json").write_text(json.dumps({
        "id": job_id, "kind": "model" if mode == "model" else "image", "mode": mode,
        "status": "queued", "description": description[:160], "created_at": time.time(),
    }, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_run, args=(job_id, mode, description, aspect, resolution), daemon=True).start()
    return _read(job_id)


@router.get("")
def history():
    if not ROOT.exists():
        return {"jobs": []}
    jobs = []
    for path in ROOT.glob("*/status.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in {"queued", "uploading", "processing"} and time.time() - job["created_at"] > 3600:
                job = _update(job["id"], status="error", error="Geração interrompida. Envie novamente.")
            jobs.append(job)
        except (OSError, ValueError, KeyError):
            continue
    return {"jobs": sorted(jobs, key=lambda job: job["created_at"], reverse=True)[:80]}


@router.get("/{job_id}")
def status(job_id: str):
    return _read(job_id)


@router.get("/{job_id}/file")
def file(job_id: str, download: bool = False):
    job = _read(job_id)
    if job.get("status") != "done":
        raise HTTPException(404, "Resultado ainda não disponível")
    suffix = ".glb" if job["kind"] == "model" else ".png"
    path = _folder(job_id) / ("result" + suffix)
    if not path.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    mime = "model/gltf-binary" if suffix == ".glb" else "image/png"
    return FileResponse(path, media_type=mime, filename=f"influencer-{job_id[:8]}{suffix}" if download else None)
