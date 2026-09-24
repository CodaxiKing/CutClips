"""Local-only ComfyUI workflow builders and validation."""
from __future__ import annotations

import copy
import json
import os
import secrets
from pathlib import Path


PAID_MARKERS = (
    "kling", "gemini", "nano banana", "tencentimagetomodel", "openai",
    "recraft", "meshy", "rodin", "tripo", "replicate", "runway",
    "vidu", "falai", "fal_", "api", "url", "http",
)


def validate_local_workflow(flow: dict) -> dict:
    if not isinstance(flow, dict) or not flow:
        raise ValueError("Fluxo ComfyUI vazio ou inválido")
    for node_id, node in flow.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise ValueError(f"Nó {node_id} inválido no fluxo ComfyUI")
        kind = node.get("class_type", "")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"Nó {node_id} sem class_type")
        normalized = kind.lower()
        if any(marker in normalized for marker in PAID_MARKERS):
            raise ValueError(f"Nó externo ou pago bloqueado: {kind}")
        if any(isinstance(value, str) and value.lower().startswith(("http://", "https://"))
               for value in node["inputs"].values()):
            raise ValueError(f"URL externa bloqueada no nó {node_id}")
    return flow


def load_local_template(env_name: str) -> dict:
    configured = os.getenv(env_name, "").strip()
    if not configured:
        raise ValueError(f"Configure {env_name} com um fluxo local exportado em formato API do ComfyUI")
    path = Path(configured).expanduser()
    if not path.is_file():
        raise ValueError(f"Fluxo local não encontrado: {path}")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Não foi possível ler o fluxo local: {path}") from exc
    return validate_local_workflow(copy.deepcopy(content))


def find_nodes(flow: dict, class_type: str) -> list[tuple[str, dict]]:
    return [(key, value) for key, value in flow.items() if value["class_type"] == class_type]


def local_video_workflow(image: str, video: str, prompt: str) -> tuple[dict, str]:
    flow = load_local_template("CUTCLIPS_WAN_WORKFLOW")
    images, videos, saves = (find_nodes(flow, name) for name in ("LoadImage", "LoadVideo", "SaveVideo"))
    if len(images) != 1 or len(videos) != 1 or len(saves) != 1:
        raise ValueError("O fluxo Wan precisa ter um LoadImage, um LoadVideo e um SaveVideo")
    images[0][1]["inputs"]["image"] = image
    videos[0][1]["inputs"]["file"] = video
    saves[0][1]["inputs"]["filename_prefix"] = "video/cutclips-motion-control"
    text = positive_text_node(flow)
    if text:
        text["inputs"]["text"] = prompt
    return flow, saves[0][0]


WAN_NEGATIVE = (
    "vivid colors, overexposed, static, blurry details, subtitles, style, artwork, painting, still frame, "
    "grey overall, worst quality, low quality, JPEG artifacts, ugly, deformed, extra fingers, poorly drawn hands, "
    "poorly drawn face, malformed limbs, fused fingers, motionless, cluttered background, three legs, crowd, walking backwards"
)
WAN_FPS = 16
WAN_MAX_FRAMES = 77  # uma passada do Wan Animate; estender exige mais passadas e não cabe em 8 GB


def wan_models() -> dict[str, str]:
    return {
        "unet": os.getenv("CUTCLIPS_WAN_MODEL", "Wan2.2-Animate-14B-Q4_K_S.gguf"),
        "lora": os.getenv("CUTCLIPS_WAN_LORA", "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"),
        "text_encoder": os.getenv("CUTCLIPS_WAN_TEXT_ENCODER", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        "clip_vision": os.getenv("CUTCLIPS_WAN_CLIP_VISION", "clip_vision_h.safetensors"),
        "vae": os.getenv("CUTCLIPS_WAN_VAE", "wan_2.1_vae.safetensors"),
    }


def wan_size(width: int, height: int) -> tuple[int, int]:
    """Output size keeping the video's aspect, longest side capped (default 640) and multiples of 16."""
    longest = int(os.getenv("CUTCLIPS_WAN_MAX_SIDE", "640"))
    scale = longest / max(width, height)
    return max(16, round(width * scale / 16) * 16), max(16, round(height * scale / 16) * 16)


def wan_animate_workflow(image: str, video: str, prompt: str, width: int, height: int,
                         frames: int) -> tuple[dict, str]:
    """Wan 2.2 Animate "move": the person in `image` performs the motion of `video`.

    Mirrors the official ComfyUI template without the SAM2 character-replacement branch, with
    the GGUF model (ComfyUI-GGUF) and DWPose (comfyui_controlnet_aux) for pose and face.
    `video` must already be at WAN_FPS; `frames` is how many of its frames to animate.
    """
    length = max(5, (min(frames, WAN_MAX_FRAMES) - 1) // 4 * 4 + 1)
    models = wan_models()
    pose_resolution = max(64, round(min(width, height) / 64) * 64)
    dwpose = {"bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
              "resolution": pose_resolution, "scale_stick_for_xinsr_cn": "disable"}
    flow = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": models["unet"]}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": models["lora"],
                                                              "strength_model": 1.0}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["text_encoder"], "type": "wan",
                                                     "device": "default"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": WAN_NEGATIVE, "clip": ["4", 0]}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "8": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": models["clip_vision"]}},
        "9": {"class_type": "LoadImage", "inputs": {"image": image}},
        "10": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["8", 0], "image": ["9", 0],
                                                            "crop": "none"}},
        "11": {"class_type": "LoadVideo", "inputs": {"file": video}},
        "12": {"class_type": "GetVideoComponents", "inputs": {"video": ["11", 0]}},
        "13": {"class_type": "ImageScale", "inputs": {"image": ["12", 0], "upscale_method": "lanczos",
                                                      "width": width, "height": height, "crop": "center"}},
        "14": {"class_type": "DWPreprocessor", "inputs": {"image": ["13", 0], "detect_hand": "enable",
                                                          "detect_body": "enable", "detect_face": "disable", **dwpose}},
        "15": {"class_type": "DWPreprocessor", "inputs": {"image": ["13", 0], "detect_hand": "disable",
                                                          "detect_body": "disable", "detect_face": "enable", **dwpose}},
        "16": {"class_type": "WanAnimateToVideo", "inputs": {
            "positive": ["5", 0], "negative": ["6", 0], "vae": ["7", 0], "width": width, "height": height,
            "length": length, "batch_size": 1, "continue_motion_max_frames": 5, "video_frame_offset": 0,
            "clip_vision_output": ["10", 0], "reference_image": ["9", 0],
            "pose_video": ["14", 0], "face_video": ["15", 0]}},
        "17": {"class_type": "KSampler", "inputs": {
            "model": ["3", 0], "positive": ["16", 0], "negative": ["16", 1], "latent_image": ["16", 2],
            "seed": secrets.randbits(48), "steps": 6, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "denoise": 1.0}},
        "18": {"class_type": "TrimVideoLatent", "inputs": {"samples": ["17", 0], "trim_amount": ["16", 3]}},
        "19": {"class_type": "VAEDecode", "inputs": {"samples": ["18", 0], "vae": ["7", 0]}},
        "20": {"class_type": "ImageFromBatch", "inputs": {"image": ["19", 0], "batch_index": ["16", 4],
                                                          "length": 4096}},
        "21": {"class_type": "CreateVideo", "inputs": {"images": ["20", 0], "fps": float(WAN_FPS)}},
        "30": {"class_type": "SaveVideo", "inputs": {"video": ["21", 0], "format": "mp4",
                                                     "filename_prefix": "video/cutclips-motion-control"}},
    }
    return validate_local_workflow(flow), "30"


def positive_text_node(flow: dict) -> dict | None:
    """The CLIPTextEncode wired to a `positive` input; Wan templates also carry a negative one."""
    texts = dict(find_nodes(flow, "CLIPTextEncode"))
    for node in flow.values():
        link = node["inputs"].get("positive")
        if isinstance(link, list) and link and str(link[0]) in texts:
            return texts[str(link[0])]
    return next(iter(texts.values()), None)


def local_3d_workflow(images: dict[str, str]) -> tuple[dict, str]:
    flow = load_local_template("CUTCLIPS_HUNYUAN3D_WORKFLOW")
    loads, saves = find_nodes(flow, "LoadImage"), find_nodes(flow, "SaveGLB")
    if len(loads) < 1 or len(saves) != 1:
        raise ValueError("O fluxo Hunyuan3D precisa ter LoadImage e um SaveGLB")
    names = [images[name] for name in ("person", "left", "right", "back") if name in images]
    if len(names) != len(loads):
        raise ValueError("O número de fotos deve corresponder aos LoadImage do fluxo 3D")
    for (_, node), name in zip(loads, names):
        node["inputs"]["image"] = name
    saves[0][1]["inputs"]["filename_prefix"] = "3d/cutclips-influencer"
    return flow, saves[0][0]


def flux_image_workflow(prompt: str, aspect: str, resolution: str, images: list[str]) -> tuple[dict, str]:
    """Four-step FLUX.2 Klein 4B workflow using only local ComfyUI core nodes."""
    ratios = {"1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2), "3:4": (3, 4),
              "4:3": (4, 3), "4:5": (4, 5), "9:16": (9, 16), "16:9": (16, 9)}
    x, y = ratios[aspect]
    longest = 1024 if resolution == "1K" else 2048
    width = max(64, round(longest * x / max(x, y) / 16) * 16)
    height = max(64, round(longest * y / max(x, y) / 16) * 16)
    model = os.getenv("CUTCLIPS_FLUX_MODEL", "flux-2-klein-4b-fp8.safetensors")
    encoder = os.getenv("CUTCLIPS_FLUX_TEXT_ENCODER", "qwen_3_4b.safetensors")
    vae = os.getenv("CUTCLIPS_FLUX_VAE", "flux2-vae.safetensors")
    flow = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": encoder, "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "CFGGuider", "inputs": {"cfg": 1, "model": ["1", 0],
                                                    "positive": ["4", 0], "negative": ["5", 0]}},
        "7": {"class_type": "Flux2Scheduler", "inputs": {"steps": 4, "width": width, "height": height}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": secrets.randbits(32)}},
        "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["6", 0],
                                                            "sampler": ["8", 0], "sigmas": ["7", 0],
                                                            "latent_image": ["10", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "20": {"class_type": "SaveImage", "inputs": {"images": ["12", 0],
                                                  "filename_prefix": "cutclips-influencer"}},
    }
    positive, negative = ["4", 0], ["5", 0]
    for index, name in enumerate(images):
        base = 30 + index * 4
        flow[str(base)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        flow[str(base + 1)] = {"class_type": "VAEEncode", "inputs": {"pixels": [str(base), 0], "vae": ["3", 0]}}
        flow[str(base + 2)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": positive,
                                                                              "latent": [str(base + 1), 0]}}
        flow[str(base + 3)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": negative,
                                                                              "latent": [str(base + 1), 0]}}
        positive, negative = [str(base + 2), 0], [str(base + 3), 0]
    flow["6"]["inputs"].update(positive=positive, negative=negative)
    return validate_local_workflow(flow), "20"
