"""Motion Control, troca de roupa, lip-sync e vídeo de vendas via local ComfyUI.

Quatro produtos no mesmo roteador, todos locais e sem créditos:
  * Motion Control clássico: foto + vídeo -> vídeo (Wan Animate).
  * Troca de roupa: duas etapas -- o FLUX veste (ou apresenta) a peça na foto e o Wan anima.
  * Lip-sync: a foto ganha lábios sincronizados com um áudio, ou com texto
    sintetizado pela voz salva como a da influencer.
  * Vídeo de vendas one-shot: um job entrega dois vídeos -- a influencer dançando
    com o produto e ela falando o roteiro de venda em lip-sync.
"""
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

from api.local_comfy import (WAN_FPS, WAN_MAX_FRAMES, find_nodes, flux_image_workflow,
                              load_local_template, local_video_workflow,
                              wan_animate_workflow, wan_models, wan_size)
from cutclips.config import STORAGE
from cutclips.narrate import audio_duration, influencer_voice, reply_script, sell_script, speak
from cutclips.probe import ProbeError, probe

router = APIRouter(prefix="/api/motion-control", tags=["Motion Control"])
ROOT = Path(STORAGE) / "motion-control"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".webm", ".m4v"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
MAX_IMAGE = 15 * 1024 * 1024
MAX_VIDEO = 150 * 1024 * 1024
MAX_AUDIO = 30 * 1024 * 1024
MAX_RESULT = 600 * 1024 * 1024
MAX_PROMPT = 2500
MAX_SPEECH_CHARS = 1000
MAX_SPEECH_SECONDS = 60
MAX_REPLY = 2000
DEFAULT_MOTION_PROMPT = "A pessoa da imagem executa os movimentos do vídeo de referência."
COMPOSED_NAME = "person-outfit.png"
# Etapa 1 da troca de roupa: FLUX veste a peça na foto antes do Wan animar.
FLUX_NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "Flux2Scheduler", "ReferenceLatent", "SaveImage")
FLUX_MODELS = (("UNETLoader", "unet_name", "CUTCLIPS_FLUX_MODEL", "flux-2-klein-4b-fp8.safetensors"),
               ("CLIPLoader", "clip_name", "CUTCLIPS_FLUX_TEXT_ENCODER", "qwen_3_4b.safetensors"),
               ("VAELoader", "vae_name", "CUTCLIPS_FLUX_VAE", "flux2-vae.safetensors"))
ACTIVE_STATUS = {"queued", "speaking", "dressing", "uploading", "processing", "talking"}
# RLock: o retry lê e regrava o status.json dentro da seção crítica sem deadlock.
_lock = threading.RLock()


class Cancelled(RuntimeError):
    """Cancelamento pedido pelo usuário: encerra o job sem virar erro."""


def _cancelled(job_id: str) -> bool:
    return (ROOT / job_id / "cancel.flag").exists()


def _check_cancel(job_id: str) -> None:
    """Levanta Cancelled quando a bandeira do cancelamento existe."""
    if _cancelled(job_id):
        raise Cancelled


def _stage_done(job_id: str, flag: str, artifact: Path) -> bool:
    """Retomada: a etapa só é pulada se a bandeira do status.json existe E o arquivo está completo."""
    try:
        data = json.loads((ROOT / job_id / "status.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return bool(data.get(flag)) and artifact.is_file() and artifact.stat().st_size > 0


def _fail(job_id: str, exc: Exception) -> None:
    """Erro de job: cancelamento pendente vira 'cancelled'; FFmpeg mantém a mensagem dele."""
    if _cancelled(job_id):
        _save(job_id, status="cancelled")
    elif isinstance(exc, subprocess.CalledProcessError):
        _save(job_id, status="error", error="FFmpeg falhou: " + exc.stderr.decode(errors="replace")[-500:])
    else:
        _save(job_id, status="error", error=str(exc)[:1000])


def _finish(job_id: str, **changes) -> dict:
    """Conclusão que respeita um cancelamento pedido durante o último estágio."""
    return _save(job_id, status="cancelled" if _cancelled(job_id) else "done", **changes)


def _guard_speech(path: Path) -> None:
    """TTS medido antes do ComfyUI: texto longo falha aqui, em segundos, não depois do render."""
    seconds = audio_duration(path)
    if not 1 <= seconds <= MAX_SPEECH_SECONDS:
        raise RuntimeError(f"A fala gerada tem {seconds:.0f} s e o lip-sync aceita de 1 a {MAX_SPEECH_SECONDS} s. "
                           "Encurte o texto e tente novamente.")


def _speech_audio(folder: Path) -> Path:
    """O áudio da fala: só extensão de áudio — o TTS deixa um `speech.txt` ao lado
    do wav, e subir o .txt como áudio faria o ComfyUI recusar o LoadAudio."""
    return next(path for path in folder.glob("speech.*") if path.suffix in AUDIO_EXT)


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


def _flux_status(info: dict) -> tuple[bool, list[str]]:
    """Nós e modelos que a etapa 1 (FLUX veste a roupa) precisa ter no ComfyUI local."""
    missing = [node for node in FLUX_NODES if node not in info]

    def available(node: str, field: str, name: str) -> bool:
        try:
            return name in info[node]["input"]["required"][field][0]
        except (KeyError, IndexError, TypeError):
            return False

    for node, field, env, default in FLUX_MODELS:
        name = os.getenv(env, default)
        if not available(node, field, name):
            missing.append(name)
    return not missing, missing


def _aspect_for(width: int, height: int) -> str:
    """Formato FLUX mais próximo do vídeo de movimento, para a foto vestida combinar com ele."""
    ratios = {"1:1": 1.0, "2:3": 2 / 3, "3:2": 3 / 2, "3:4": 3 / 4, "4:3": 4 / 3,
              "4:5": 4 / 5, "9:16": 9 / 16, "16:9": 16 / 9}
    ratio = width / max(1, height)
    return min(ratios, key=lambda key: abs(ratios[key] - ratio))


def _submit(client: httpx.Client, flow: dict, job_id: str) -> str:
    # Cancelamento checado aqui: um job cancelado não manda render novo para o ComfyUI.
    _check_cancel(job_id)
    response = client.post("/prompt", json={"prompt": flow, "client_id": job_id})
    if response.is_error:
        raise RuntimeError(f"ComfyUI recusou o fluxo ({response.status_code}): {response.text[:500]}")
    body = response.json()
    if "error" in body:
        raise RuntimeError(str(body["error"]))
    return body["prompt_id"]


def _await_entry(client: httpx.Client, prompt_id: str, output_id: str, *, job_id: str,
                 output_keys: tuple[str, ...], minutes: int, missing: str, interval: int = 4):
    """Espera o histórico do ComfyUI devolver a saída pedida e devolve a primeira entrada."""
    deadline = time.monotonic() + 60 * minutes
    while time.monotonic() < deadline:
        _check_cancel(job_id)
        time.sleep(interval)
        response = client.get(f"/history/{prompt_id}")
        response.raise_for_status()
        item = response.json().get(prompt_id)
        if not item:
            continue
        if item.get("status", {}).get("status_str") == "error":
            messages = item.get("status", {}).get("messages", [])
            raise RuntimeError(str(messages[-1] if messages else "Falha no ComfyUI"))
        outputs = item.get("outputs", {}).get(output_id, {})
        entries = next((outputs[key] for key in output_keys if outputs.get(key)), None)
        if entries:
            return entries[0]
        if item.get("status", {}).get("completed"):
            raise RuntimeError(missing)
    raise RuntimeError(f"Tempo de espera excedido no ComfyUI ({minutes} min)")


def _stream(client: httpx.Client, entry: dict, target: Path, limit: int, too_big: str,
            job_id: str = "") -> None:
    with client.stream("GET", "/view", params={
        "filename": entry["filename"], "subfolder": entry.get("subfolder", ""),
        "type": entry.get("type", "output"),
    }) as download:
        download.raise_for_status()
        size = 0
        try:
            with target.open("wb") as out:
                for chunk in download.iter_bytes(1024 * 1024):
                    if job_id:
                        _check_cancel(job_id)
                    size += len(chunk)
                    if size > limit:
                        target.unlink(missing_ok=True)
                        raise RuntimeError(too_big)
                    out.write(chunk)
        except Cancelled:
            target.unlink(missing_ok=True)
            raise


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
    result = folder / "result.mp4"
    try:
        _check_cancel(job_id)
        if _stage_done(job_id, "done_dance", result):
            _finish(job_id)
            return
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
            prompt_id = _submit(client, workflow, job_id)
            _save(job_id, status="processing", prompt_id=prompt_id)
            entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("videos", "gifs"),
                                 minutes=90, interval=5,
                                 missing="O ComfyUI concluiu a tarefa sem devolver um vídeo. Verifique o nó SaveVideo.")
            _stream(client, entry, result, MAX_RESULT, "O vídeo gerado excede 600 MB", job_id=job_id)
            _add_audio(result, prepared)
            _finish(job_id, done_dance=True)
    except Exception as exc:
        _fail(job_id, exc)


def _tryon_flux_flow(person: str, outfit: str, description: str, aspect: str, mode: str = "outfit") -> tuple[dict, str]:
    """Etapa 1: a pessoa da imagem 1 veste a peça (modo roupa) ou apresenta o produto (modo produto) na imagem 2."""
    from api.influencers import _prompt  # import tardio: api.influencers já importa este módulo
    text = description.strip() or ("Present the product exactly as in image 2."
                                   if mode == "product" else "Match the garment exactly.")
    return flux_image_workflow(_prompt(mode, text, False), aspect, "1K", [person, outfit])


def _person_source(job_id: str) -> Path:
    """Foto pronta de uma geração da Influencer IA (import tardio pelo mesmo motivo acima)."""
    from api.influencers import _source_image
    return _source_image(job_id)


def _run_tryon(job_id: str, description: str, mode: str = "outfit") -> None:
    """Etapa 1: FLUX veste a roupa (ou aplica o produto) na foto. Etapa 2: Wan Animate anima."""
    from api.influencers import _reference  # import tardio: api.influencers já importa este módulo
    folder = ROOT / job_id
    composed = folder / COMPOSED_NAME
    result = folder / "result.mp4"
    try:
        _check_cancel(job_id)
        dressed = _stage_done(job_id, "done_dress", composed)
        if dressed and _stage_done(job_id, "done_dance", result):
            _finish(job_id)
            return
        with httpx.Client(base_url=_base_url(), timeout=httpx.Timeout(60, read=180)) as client:
            info = client.get("/object_info")
            info.raise_for_status()
            info = info.json()
            if not _custom_template() and (missing := _builtin_missing(info)):
                raise RuntimeError("Wan Animate incompleto no ComfyUI. Faltando: " + ", ".join(missing))
            source = next(path for path in folder.glob("motion.*") if path.stem == "motion")

            # Etapa 1 -- a influencer já sai da foto vestindo a peça escolhida.
            # Retomada: se a foto composta já existe, o FLUX não roda de novo.
            if not dressed:
                flux_ready, flux_missing = _flux_status(info)
                if not flux_ready:
                    raise RuntimeError("FLUX incompleto no ComfyUI. Faltando: " + ", ".join(flux_missing))
                media = probe(source)
                _save(job_id, status="dressing")
                person_file = next(path for path in folder.glob("person.*") if path.stem == "person")
                outfit_file = next(path for path in folder.glob("outfit.*") if path.stem == "outfit")
                person = _upload(client, _reference(person_file))
                outfit = _upload(client, _reference(outfit_file))
                flow, output_id = _tryon_flux_flow(person, outfit, description,
                                                   _aspect_for(media.width, media.height), mode)
                prompt_id = _submit(client, flow, job_id)
                _save(job_id, status="dressing", prompt_id=prompt_id)
                entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("images",),
                                     minutes=45,
                                     missing="O ComfyUI concluiu a etapa da roupa sem devolver uma imagem.")
                _stream(client, entry, composed, MAX_RESULT, "A imagem da etapa 1 excede 600 MB", job_id=job_id)
                _save(job_id, composed=COMPOSED_NAME, done_dress=True)

            # Etapa 2 -- a foto já vestida segue os movimentos do vídeo de dança.
            _save(job_id, status="uploading")
            prepared = folder / "motion-16fps.mp4"
            frames = _prepare_motion(source, prepared)
            image = _upload(client, composed)
            video = _upload(client, prepared)
            if _custom_template():
                workflow, output_id = local_video_workflow(image, video, DEFAULT_MOTION_PROMPT)
            else:
                fitted = probe(prepared)
                width, height = wan_size(fitted.width, fitted.height)
                workflow, output_id = wan_animate_workflow(image, video, DEFAULT_MOTION_PROMPT,
                                                           width, height, frames)
            prompt_id = _submit(client, workflow, job_id)
            _save(job_id, status="processing", prompt_id=prompt_id)
            entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("videos", "gifs"),
                                 minutes=90, interval=5,
                                 missing="O ComfyUI concluiu a tarefa sem devolver um vídeo. Verifique o nó SaveVideo.")
            _stream(client, entry, result, MAX_RESULT, "O vídeo gerado excede 600 MB", job_id=job_id)
            _add_audio(result, prepared)
            _finish(job_id, done_dance=True)
    except Exception as exc:
        _fail(job_id, exc)


def _custom_lipsync() -> bool:
    """CUTCLIPS_LIPSYNC_WORKFLOW é obrigatório: lip-sync não tem fluxo embutido."""
    return bool(os.getenv("CUTCLIPS_LIPSYNC_WORKFLOW", "").strip())


def _lipsync_status(info: dict) -> tuple[bool, list[str]]:
    """Nós do fluxo de lip-sync que faltam nesta instalação do ComfyUI."""
    try:
        flow = load_local_template("CUTCLIPS_LIPSYNC_WORKFLOW")
    except ValueError:
        return False, ["fluxo CUTCLIPS_LIPSYNC_WORKFLOW ilegível"]
    required = {node.get("class_type", "") for node in flow.values()}
    missing = sorted(kind for kind in required if kind and kind not in info)
    return not missing, missing


def _lipsync_flow(image: str, audio: str) -> tuple[dict, str]:
    """Injeta foto e áudio no fluxo exportado em CUTCLIPS_LIPSYNC_WORKFLOW."""
    flow = load_local_template("CUTCLIPS_LIPSYNC_WORKFLOW")
    images, audios, saves = (find_nodes(flow, name) for name in ("LoadImage", "LoadAudio", "SaveVideo"))
    if len(images) != 1 or len(audios) != 1 or len(saves) != 1:
        raise ValueError("O fluxo de lip-sync precisa ter exatamente um LoadImage, um LoadAudio e um SaveVideo")
    images[0][1]["inputs"]["image"] = image
    audios[0][1]["inputs"]["audio"] = audio
    saves[0][1]["inputs"]["filename_prefix"] = "video/cutclips-lipsync"
    return flow, saves[0][0]


LIPSYNC_HINT = ("Configure CUTCLIPS_LIPSYNC_WORKFLOW com o caminho de um fluxo de lip-sync "
                "exportado em formato API do ComfyUI (LoadImage + LoadAudio + SaveVideo).")


def _run_lipsync(job_id: str, text: str, voice: str, reply_to: str = "") -> None:
    """Sintetiza a voz (texto ou resposta a comentários) e manda foto + áudio no ComfyUI."""
    folder = ROOT / job_id
    speech = folder / "speech.wav"
    try:
        _check_cancel(job_id)
        if (text or reply_to) and not _stage_done(job_id, "done_speech", speech):
            # TTS local antes de qualquer upload: falha de voz ou da IA aparece aqui, em
            # segundos, e não depois de o ComfyUI já ter começado a renderizar.
            _save(job_id, status="speaking")
            if not text:
                text = " ".join(reply_script(reply_to))
            speak(text, speech, voice)
            _guard_speech(speech)
            _save(job_id, done_speech=True)
        with httpx.Client(base_url=_base_url(), timeout=httpx.Timeout(60, read=180)) as client:
            info = client.get("/object_info")
            info.raise_for_status()
            if not _custom_lipsync():
                raise RuntimeError(LIPSYNC_HINT)
            ready, missing = _lipsync_status(info.json())
            if not ready:
                raise RuntimeError("Lip-sync incompleto no ComfyUI. Faltando: " + ", ".join(missing))
            _save(job_id, status="uploading")
            person_file = next(path for path in folder.glob("person.*") if path.stem == "person")
            audio_file = _speech_audio(folder)
            image = _upload(client, person_file)
            sound = _upload(client, audio_file)
            flow, output_id = _lipsync_flow(image, sound)
            prompt_id = _submit(client, flow, job_id)
            _save(job_id, status="processing", prompt_id=prompt_id)
            entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("videos", "gifs"),
                                 minutes=60, interval=5,
                                 missing="O ComfyUI concluiu sem devolver um vídeo. Verifique o nó SaveVideo.")
            result = folder / "result.mp4"
            _stream(client, entry, result, MAX_RESULT, "O vídeo gerado excede 600 MB", job_id=job_id)
            _finish(job_id)
    except Exception as exc:
        _fail(job_id, exc)


def _run_oneshot(job_id: str, description: str, mode: str, product_name: str,
                 benefit: str, cta: str, script: str) -> None:
    """Dois vídeos: voz primeiro (falha em segundos), FLUX se houver produto, dança e lip-sync."""
    from api.influencers import _reference  # import tardio: api.influencers já importa este módulo
    folder = ROOT / job_id
    speech = folder / "speech.wav"
    composed = folder / COMPOSED_NAME
    result = folder / "result.mp4"
    talking = folder / "talking.mp4"
    try:
        _check_cancel(job_id)
        if _stage_done(job_id, "done_talk", talking):
            _finish(job_id)
            return
        with httpx.Client(base_url=_base_url(), timeout=httpx.Timeout(60, read=180)) as client:
            info = client.get("/object_info")
            info.raise_for_status()
            info = info.json()
            has_product = bool(list(folder.glob("product.*")))
            dressed = _stage_done(job_id, "done_dress", composed)
            danced = _stage_done(job_id, "done_dance", result)
            # Cada checagem só é exigida se a etapa dela ainda vai rodar: a retomada
            # não pode falhar por falta de FLUX quando a foto composta já existe.
            if not danced and not _custom_template() and (missing := _builtin_missing(info)):
                raise RuntimeError("Wan Animate incompleto no ComfyUI. Faltando: " + ", ".join(missing))
            if has_product and not dressed:
                flux_ready, flux_missing = _flux_status(info)
                if not flux_ready:
                    raise RuntimeError("FLUX incompleto no ComfyUI. Faltando: " + ", ".join(flux_missing))
            if not _custom_lipsync():
                raise RuntimeError(LIPSYNC_HINT)
            lipsync_ready, lipsync_missing = _lipsync_status(info)
            if not lipsync_ready:
                raise RuntimeError("Lip-sync incompleto no ComfyUI. Faltando: " + ", ".join(lipsync_missing))

            source = next(path for path in folder.glob("motion.*") if path.stem == "motion")
            media = probe(source)
            source_image = next(path for path in folder.glob("person.*") if path.stem == "person")

            # Voz primeiro: roteiro + TTS levam segundos, e erro de voz não pode
            # aparecer só depois de dez minutos de render.
            if not _stage_done(job_id, "done_speech", speech):
                _save(job_id, status="speaking")
                text = script.strip() or " ".join(sell_script(product_name, benefit, cta))
                speak(text, speech, influencer_voice()["voice"])
                _guard_speech(speech)
                _save(job_id, done_speech=True)

            if has_product and not dressed:
                _save(job_id, status="dressing")
                product_file = next(path for path in folder.glob("product.*") if path.stem == "product")
                person = _upload(client, _reference(source_image))
                item = _upload(client, _reference(product_file))
                flow, output_id = _tryon_flux_flow(person, item, description,
                                                   _aspect_for(media.width, media.height), mode)
                prompt_id = _submit(client, flow, job_id)
                _save(job_id, status="dressing", prompt_id=prompt_id)
                entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("images",),
                                     minutes=45,
                                     missing="O ComfyUI concluiu a etapa do produto sem devolver uma imagem.")
                _stream(client, entry, composed, MAX_RESULT, "A imagem da etapa do produto excede 600 MB",
                        job_id=job_id)
                _save(job_id, composed=COMPOSED_NAME, done_dress=True)
                source_image = composed
            elif dressed:
                source_image = composed

            if not danced:
                # Vídeo de dança -- a primeira metade da entrega.
                _save(job_id, status="uploading")
                prepared = folder / "motion-16fps.mp4"
                frames = _prepare_motion(source, prepared)
                image = _upload(client, source_image)
                video = _upload(client, prepared)
                if _custom_template():
                    workflow, output_id = local_video_workflow(image, video, DEFAULT_MOTION_PROMPT)
                else:
                    fitted = probe(prepared)
                    width, height = wan_size(fitted.width, fitted.height)
                    workflow, output_id = wan_animate_workflow(image, video, DEFAULT_MOTION_PROMPT,
                                                               width, height, frames)
                prompt_id = _submit(client, workflow, job_id)
                _save(job_id, status="processing", prompt_id=prompt_id)
                entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("videos", "gifs"),
                                     minutes=90, interval=5,
                                     missing="O ComfyUI concluiu a tarefa sem devolver um vídeo. Verifique o nó SaveVideo.")
                _stream(client, entry, result, MAX_RESULT, "O vídeo gerado excede 600 MB", job_id=job_id)
                _add_audio(result, prepared)
                _save(job_id, status="talking", dance_ready=True, done_dance=True)

            # Lip-sync -- a segunda metade: a mesma cara falando o roteiro de venda.
            image = _upload(client, source_image)
            sound = _upload(client, speech)
            flow, output_id = _lipsync_flow(image, sound)
            prompt_id = _submit(client, flow, job_id)
            _save(job_id, status="talking", prompt_id=prompt_id)
            entry = _await_entry(client, prompt_id, output_id, job_id=job_id, output_keys=("videos", "gifs"),
                                 minutes=60, interval=5,
                                 missing="O ComfyUI concluiu sem devolver um vídeo. Verifique o nó SaveVideo.")
            _stream(client, entry, talking, MAX_RESULT, "O vídeo gerado excede 600 MB", job_id=job_id)
            _finish(job_id, done_talk=True)
    except Exception as exc:
        _fail(job_id, exc)


def _lipsync_config(info: dict | None) -> tuple[bool, list[str]]:
    """Lip-sync só existe quando CUTCLIPS_LIPSYNC_WORKFLOW aponta para um fluxo válido."""
    if not _custom_lipsync():
        return False, ["CUTCLIPS_LIPSYNC_WORKFLOW"]
    if info is None:
        return False, []
    return _lipsync_status(info)


@router.get("/config")
def configuration():
    try:
        with httpx.Client(base_url=_base_url(), timeout=3) as client:
            response = client.get("/object_info")
            response.raise_for_status()
            info = response.json()
            flux_ready, flux_missing = _flux_status(info)
            lipsync_ready, lipsync_missing = _lipsync_config(info)
            if not _custom_template():
                missing = _builtin_missing(info)
                return {"ready": not missing, "connected": True, "url": _base_url(), "template": not missing,
                        "missing": missing, "flux_ready": flux_ready, "flux_missing": flux_missing,
                        "lipsync_ready": lipsync_ready, "lipsync_missing": lipsync_missing}
            try:
                flow = load_local_template("CUTCLIPS_WAN_WORKFLOW")
                required = {node["class_type"] for node in flow.values()}
                return {"ready": required.issubset(info), "connected": True, "url": _base_url(), "template": True,
                        "flux_ready": flux_ready, "flux_missing": flux_missing,
                        "lipsync_ready": lipsync_ready, "lipsync_missing": lipsync_missing}
            except ValueError:
                return {"ready": False, "connected": True, "url": _base_url(), "template": False,
                        "flux_ready": flux_ready, "flux_missing": flux_missing,
                        "lipsync_ready": lipsync_ready, "lipsync_missing": lipsync_missing}
    except (httpx.HTTPError, ValueError):
        lipsync_ready, lipsync_missing = _lipsync_config(None)
        return {"ready": False, "connected": False, "template": False, "flux_ready": False, "flux_missing": [],
                "lipsync_ready": lipsync_ready, "lipsync_missing": lipsync_missing,
                "url": os.getenv("CUTCLIPS_COMFYUI_URL", "http://127.0.0.1:8188")}


@router.post("")
async def create(
    image: UploadFile = File(...), video: UploadFile = File(...),
    prompt: str = Form(DEFAULT_MOTION_PROMPT),
):
    if Path(image.filename or "").suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "Use uma imagem PNG, JPG ou WebP")
    if Path(video.filename or "").suffix.lower() not in VIDEO_EXT:
        raise HTTPException(422, "Use um vídeo MP4, WebM ou M4V")
    if len(prompt) > MAX_PROMPT:
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
        "run_prompt": prompt,
    }, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_run, args=(job_id, prompt), daemon=True).start()
    return _record(job_id)


@router.post("/tryon")
async def create_tryon(
    video: UploadFile = File(...), outfit: UploadFile = File(...),
    person: UploadFile | None = File(None), person_job: str = Form(""),
    prompt: str = Form(""), mode: str = Form("outfit"),
):
    """Três entradas: vídeo de dança, foto da influencer (enviada ou da Influencer IA) e foto da roupa ou do produto."""
    from api.influencers import _copy_image  # import tardio: api.influencers já importa este módulo

    if mode not in {"outfit", "product"}:
        raise HTTPException(422, "Modo inválido: use outfit (vestir) ou product (apresentar)")
    if Path(video.filename or "").suffix.lower() not in VIDEO_EXT:
        raise HTTPException(422, "Use um vídeo MP4, WebM ou M4V")
    if Path(outfit.filename or "").suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "A foto da roupa ou do produto precisa ser PNG, JPG ou WebP")
    has_person = bool(person and person.filename)
    if has_person and Path(person.filename).suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "A foto da influencer precisa ser PNG, JPG ou WebP")
    if not has_person and not person_job.strip():
        raise HTTPException(422, "Envie a foto da influencer ou escolha uma geração da Influencer IA")
    if len(prompt) > MAX_PROMPT:
        raise HTTPException(422, "Descrição longa demais")
    source = None if has_person else _person_source(person_job.strip())
    description = prompt.strip() or ("Present the product exactly as in image 2."
                                     if mode == "product" else "Match the garment exactly.")
    job_id = uuid.uuid4().hex
    folder = ROOT / job_id
    folder.mkdir(parents=True, exist_ok=False)
    try:
        video_path = folder / f"motion{Path(video.filename).suffix.lower()}"
        await _copy_upload(video, video_path, MAX_VIDEO)
        if source is not None:
            (folder / "person.png").write_bytes(source.read_bytes())
        elif await _copy_image(person, folder, "person") is None:
            raise HTTPException(422, "Envie a foto da influencer ou escolha uma geração da Influencer IA")
        if await _copy_image(outfit, folder, "outfit") is None:
            raise HTTPException(422, "Envie a imagem da roupa")
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
        "id": job_id, "kind": "tryon", "mode": mode, "status": "queued", "model": "FLUX + Wan local",
        "created_at": time.time(),
        "image_name": Path(person.filename).name if has_person else "Influencer IA",
        "outfit_name": Path(outfit.filename).name, "video_name": Path(video.filename).name,
        "description": description[:160], "run_prompt": description,
    }, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_run_tryon, args=(job_id, description, mode), daemon=True).start()
    return _record(job_id)


@router.post("/lipsync")
async def create_lipsync(
    person: UploadFile | None = File(None), person_job: str = Form(""),
    audio: UploadFile | None = File(None), text: str = Form(""), voice: str = Form(""),
    reply_to: str = Form(""),
):
    """A influencer fala: áudio enviado, texto escrito ou resposta a comentários (IA/template)."""
    from api.influencers import _copy_image  # import tardio: api.influencers já importa este módulo

    has_person = bool(person and person.filename)
    if has_person and Path(person.filename).suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "A foto da influencer precisa ser PNG, JPG ou WebP")
    if not has_person and not person_job.strip():
        raise HTTPException(422, "Envie a foto da influencer ou escolha uma geração da Influencer IA")
    has_audio = bool(audio and audio.filename)
    if has_audio and Path(audio.filename).suffix.lower() not in AUDIO_EXT:
        raise HTTPException(422, "Use um áudio WAV, MP3, M4A, AAC, OGG ou FLAC")
    speech = text.strip()
    reply = reply_to.strip()
    if not has_audio and not speech and not reply:
        raise HTTPException(422, "Envie um áudio, escreva o texto ou cole os comentários para responder")
    if len(speech) > MAX_SPEECH_CHARS:
        raise HTTPException(422, f"O texto pode ter no máximo {MAX_SPEECH_CHARS} caracteres")
    if len(reply) > MAX_REPLY:
        raise HTTPException(422, f"Os comentários podem ter no máximo {MAX_REPLY} caracteres")
    source = None if has_person else _person_source(person_job.strip())
    # Sem voz escolhida, o TTS usa o perfil salvo como o da influencer.
    chosen_voice = voice.strip() or (influencer_voice()["voice"] if not has_audio else "")
    job_id = uuid.uuid4().hex
    folder = ROOT / job_id
    folder.mkdir(parents=True, exist_ok=False)
    try:
        if source is not None:
            (folder / "person.png").write_bytes(source.read_bytes())
        elif await _copy_image(person, folder, "person") is None:
            raise HTTPException(422, "Envie a foto da influencer ou escolha uma geração da Influencer IA")
        if has_audio:
            speech_path = folder / f"speech{Path(audio.filename).suffix.lower()}"
            await _copy_upload(audio, speech_path, MAX_AUDIO)
            try:
                seconds = audio_duration(speech_path)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                raise HTTPException(422, "O áudio enviado não pôde ser lido") from exc
            if not 1 <= seconds <= MAX_SPEECH_SECONDS:
                raise HTTPException(422, f"O áudio precisa ter entre 1 e {MAX_SPEECH_SECONDS} segundos")
    except Exception:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
        raise
    # Parâmetros completos (sem truncar) para o retry retomar o mesmo job.
    run_text = "" if has_audio else speech
    run_reply = "" if has_audio or run_text else reply
    (folder / "status.json").write_text(json.dumps({
        "id": job_id, "kind": "lipsync", "status": "queued", "model": "Lip-sync local",
        "created_at": time.time(),
        "image_name": Path(person.filename).name if has_person else "Influencer IA",
        "audio_name": Path(audio.filename).name if has_audio else "",
        "description": speech[:160] if not has_audio else "",
        "reply": reply[:160] if reply and not speech else "",
        "voice": chosen_voice if not has_audio else "",
        "run_text": run_text, "run_reply": run_reply,
    }, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_run_lipsync,
                     args=(job_id, run_text, chosen_voice, run_reply),
                     daemon=True).start()
    return _record(job_id)


@router.post("/oneshot")
async def create_oneshot(
    video: UploadFile = File(...),
    person: UploadFile | None = File(None), person_job: str = Form(""),
    product: UploadFile | None = File(None),
    product_name: str = Form(...), benefit: str = Form(""), cta: str = Form(""),
    script: str = Form(""), mode: str = Form("product"), prompt: str = Form(""),
):
    """Dois vídeos num job: a influencer dançando com o produto e ela falando o roteiro de venda."""
    from api.influencers import _copy_image  # import tardio: api.influencers já importa este módulo

    if mode not in {"outfit", "product"}:
        raise HTTPException(422, "Modo inválido: use outfit (vestir) ou product (apresentar)")
    if Path(video.filename or "").suffix.lower() not in VIDEO_EXT:
        raise HTTPException(422, "Use um vídeo MP4, WebM ou M4V")
    name = " ".join(product_name.split())
    if not 3 <= len(name) <= 120:
        raise HTTPException(422, "Informe o nome do produto (3 a 120 caracteres)")
    if len(benefit) > 200 or len(cta) > 200:
        raise HTTPException(422, "Benefício ou chamada longos demais")
    if len(script) > MAX_SPEECH_CHARS:
        raise HTTPException(422, f"O roteiro pode ter no máximo {MAX_SPEECH_CHARS} caracteres")
    if len(prompt) > MAX_PROMPT:
        raise HTTPException(422, "Descrição longa demais")
    has_person = bool(person and person.filename)
    if has_person and Path(person.filename).suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "A foto da influencer precisa ser PNG, JPG ou WebP")
    if not has_person and not person_job.strip():
        raise HTTPException(422, "Envie a foto da influencer ou escolha uma geração da Influencer IA")
    has_product = bool(product and product.filename)
    if has_product and Path(product.filename).suffix.lower() not in IMAGE_EXT:
        raise HTTPException(422, "A foto do produto precisa ser PNG, JPG ou WebP")
    # Sem o fluxo de lip-sync não há segundo vídeo: avisa antes de minutos de render.
    if not _custom_lipsync():
        raise HTTPException(503, LIPSYNC_HINT)
    try:
        load_local_template("CUTCLIPS_LIPSYNC_WORKFLOW")
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    source = None if has_person else _person_source(person_job.strip())
    description = prompt.strip() or ("Present the product exactly as in image 2."
                                     if mode == "product" else "Match the garment exactly.")
    job_id = uuid.uuid4().hex
    folder = ROOT / job_id
    folder.mkdir(parents=True, exist_ok=False)
    try:
        video_path = folder / f"motion{Path(video.filename).suffix.lower()}"
        await _copy_upload(video, video_path, MAX_VIDEO)
        if source is not None:
            (folder / "person.png").write_bytes(source.read_bytes())
        elif await _copy_image(person, folder, "person") is None:
            raise HTTPException(422, "Envie a foto da influencer ou escolha uma geração da Influencer IA")
        if has_product and await _copy_image(product, folder, "product") is None:
            raise HTTPException(422, "Envie a imagem do produto")
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
        "id": job_id, "kind": "oneshot", "mode": mode, "status": "queued",
        "model": "FLUX + Wan + Lip-sync local", "created_at": time.time(),
        "image_name": Path(person.filename).name if has_person else "Influencer IA",
        "outfit_name": Path(product.filename).name if has_product else "",
        "video_name": Path(video.filename).name,
        "product_name": name, "description": description[:160], "run_prompt": description,
        "benefit": benefit.strip(), "cta": cta.strip(), "script": script,
    }, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_run_oneshot, args=(job_id, description, mode, name,
                                                benefit.strip(), cta.strip(), script),
                     daemon=True).start()
    return _record(job_id)


@router.get("")
def history():
    if not ROOT.exists():
        return {"jobs": []}
    jobs = []
    for path in ROOT.glob("*/status.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in ACTIVE_STATUS and time.time() - job["created_at"] > 3600:
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


@router.post("/{job_id}/cancel")
def cancel(job_id: str):
    """Para um job em andamento: sinaliza a thread e interrompe o prompt no ComfyUI."""
    if not job_id.isalnum():
        raise HTTPException(404)
    job = _record(job_id)
    if job["status"] not in ACTIVE_STATUS:
        raise HTTPException(409, "Esta geração não está em andamento")
    (ROOT / job_id / "cancel.flag").touch()
    try:
        httpx.post(_base_url() + "/interrupt", timeout=5)
    except (httpx.HTTPError, ValueError):
        pass  # a bandeira local já basta: a thread para na próxima checagem
    return _save(job_id, status="cancelled")


@router.post("/{job_id}/retry")
def retry(job_id: str):
    """Retoma um job com erro ou cancelado pulando as etapas que já terminaram."""
    if not job_id.isalnum():
        raise HTTPException(404)
    with _lock:
        job = _record(job_id)
        if job["status"] in ACTIVE_STATUS:
            raise HTTPException(409, "Esta geração já está em andamento")
        if job["status"] not in {"error", "cancelled"}:
            raise HTTPException(409, "Só é possível tentar de novo uma geração com erro ou cancelada")
        (ROOT / job_id / "cancel.flag").unlink(missing_ok=True)
        _save(job_id, status="queued", created_at=time.time(), error="")
        kind = job.get("kind", "motion")
        if kind == "tryon":
            target, args = _run_tryon, (job_id, job.get("run_prompt", ""), job.get("mode", "outfit"))
        elif kind == "lipsync":
            target, args = _run_lipsync, (job_id, job.get("run_text", ""), job.get("voice", ""),
                                           job.get("run_reply", ""))
        elif kind == "oneshot":
            target, args = _run_oneshot, (job_id, job.get("run_prompt", ""), job.get("mode", "product"),
                                          job.get("product_name", ""), job.get("benefit", ""),
                                          job.get("cta", ""), job.get("script", ""))
        else:
            target, args = _run, (job_id, job.get("run_prompt", DEFAULT_MOTION_PROMPT))
    threading.Thread(target=target, args=args, daemon=True).start()
    return _record(job_id)


@router.get("/{job_id}/composed")
def composed(job_id: str):
    """Foto da etapa 1: a influencer já vestindo a roupa, antes da animação."""
    if not job_id.isalnum():
        raise HTTPException(404)
    path = ROOT / job_id / COMPOSED_NAME
    if not path.is_file():
        raise HTTPException(404, "Foto da etapa 1 ainda não disponível")
    return FileResponse(path, media_type="image/png")


@router.get("/{job_id}/talking")
def talking_view(job_id: str):
    """Vídeo 2 do one-shot: a influencer falando o roteiro de venda em lip-sync."""
    if not job_id.isalnum() or not (ROOT / job_id / "talking.mp4").is_file():
        raise HTTPException(404, "Vídeo falando ainda não disponível")
    return FileResponse(ROOT / job_id / "talking.mp4", media_type="video/mp4")


@router.get("/{job_id}/talking/download")
def talking_download(job_id: str):
    if not job_id.isalnum() or not (ROOT / job_id / "talking.mp4").is_file():
        raise HTTPException(404, "Vídeo falando ainda não disponível")
    return FileResponse(ROOT / job_id / "talking.mp4", media_type="video/mp4",
                        filename=f"venda-falando-{job_id[:8]}.mp4")


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
