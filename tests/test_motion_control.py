"""Motion Control request validation without remote credits."""
import io
import json
from types import SimpleNamespace

from PIL import Image

from api import motion_control


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (160, 160), (12, 34, 56)).save(buffer, "PNG")
    return buffer.getvalue()


def test_motion_control_rejects_long_video(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    monkeypatch.setattr(motion_control, "probe", lambda path: SimpleNamespace(duration=31))
    workflow_path = tmp_path / "wan.json"
    workflow_path.write_text(json.dumps(_template()), encoding="utf-8")
    monkeypatch.setenv("CUTCLIPS_WAN_WORKFLOW", str(workflow_path))
    response = client.post("/api/motion-control", files={
        "image": ("person.png", b"image", "image/png"),
        "video": ("motion.mp4", b"video", "video/mp4"),
    })
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*/status.json"))


def _template():
    return {"1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
            "2": {"class_type": "LoadVideo", "inputs": {"file": "old.mp4"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "4": {"class_type": "SaveVideo", "inputs": {"video": ["5", 0]}}}


def test_motion_control_workflow_uses_uploaded_image_and_video(tmp_path, monkeypatch):
    path = tmp_path / "wan.json"
    path.write_text(json.dumps(_template()), encoding="utf-8")
    monkeypatch.setenv("CUTCLIPS_WAN_WORKFLOW", str(path))
    workflow = motion_control._workflow("person.png", "motion.mp4", "dance")
    assert workflow["1"]["inputs"]["image"] == "person.png"
    assert workflow["2"]["inputs"]["file"] == "motion.mp4"
    assert workflow["3"]["inputs"]["text"] == "dance"
    assert workflow["4"]["inputs"]["video"] == ["5", 0]


def test_motion_control_prompt_goes_to_positive_encoder(tmp_path, monkeypatch):
    template = _template()
    template["3"]["inputs"]["text"] = "blurry, low quality"
    template["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}}
    template["7"] = {"class_type": "WanAnimateToVideo", "inputs": {"positive": ["6", 0], "negative": ["3", 0]}}
    path = tmp_path / "wan.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    monkeypatch.setenv("CUTCLIPS_WAN_WORKFLOW", str(path))
    workflow = motion_control._workflow("person.png", "motion.mp4", "dance")
    assert workflow["6"]["inputs"]["text"] == "dance"
    assert workflow["3"]["inputs"]["text"] == "blurry, low quality"


def test_builtin_wan_workflow_uses_local_gguf_and_pose(monkeypatch):
    from api.local_comfy import wan_animate_workflow, wan_size
    width, height = wan_size(1080, 1920)
    assert (width, height) == (352, 640)
    flow, output = wan_animate_workflow("person.png", "motion.mp4", "dance", width, height, frames=200)
    assert flow[output]["class_type"] == "SaveVideo"
    assert flow["1"]["class_type"] == "UnetLoaderGGUF"
    assert flow["16"]["inputs"]["length"] == 77
    assert flow["16"]["inputs"]["pose_video"] == ["14", 0]
    assert flow["5"]["inputs"]["text"] == "dance"
    short, _ = wan_animate_workflow("person.png", "motion.mp4", "dance", width, height, frames=40)
    assert short["16"]["inputs"]["length"] == 37


def test_tryon_rejects_long_video(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    monkeypatch.setattr(motion_control, "probe", lambda path: SimpleNamespace(duration=31))
    response = client.post("/api/motion-control/tryon", files={
        "video": ("motion.mp4", b"video", "video/mp4"),
        "person": ("person.png", _png(), "image/png"),
        "outfit": ("outfit.png", _png(), "image/png"),
    })
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*/status.json"))


def test_tryon_requires_a_person_source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/tryon", files={
        "video": ("motion.mp4", b"video", "video/mp4"),
        "outfit": ("outfit.png", _png(), "image/png"),
    })
    assert response.status_code == 422
    assert "influencer" in response.json()["detail"].lower()
    assert not list((tmp_path / "motion").glob("*"))


def test_tryon_requires_the_outfit_image(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/tryon", files={
        "video": ("motion.mp4", b"video", "video/mp4"),
        "person": ("person.png", _png(), "image/png"),
    })
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*"))


def test_tryon_flux_workflow_dresses_person_with_outfit():
    flow, output = motion_control._tryon_flux_flow("person-ref.png", "outfit-ref.png", "vestido vermelho", "9:16")
    assert flow[output]["class_type"] == "SaveImage"
    loads = [node["inputs"]["image"] for node in flow.values() if node["class_type"] == "LoadImage"]
    assert loads == ["person-ref.png", "outfit-ref.png"]
    texts = [node["inputs"]["text"] for node in flow.values() if node["class_type"] == "CLIPTextEncode"]
    prompt = next(text for text in texts if "vestido vermelho" in text)
    assert "clothing reference" in prompt
    assert "identity reference" not in prompt


def test_tryon_product_mode_holds_the_product_in_the_prompt():
    """Modo produto: a peça não é vestida — é apresentada para a câmera."""
    flow, _ = motion_control._tryon_flux_flow("person-ref.png", "product-ref.png", "mostre o perfume",
                                              "9:16", "product")
    texts = [node["inputs"]["text"] for node in flow.values() if node["class_type"] == "CLIPTextEncode"]
    prompt = next(text for text in texts if "mostre o perfume" in text)
    assert "product being advertised" in prompt
    assert "showcasing it" in prompt
    assert "clothing reference" not in prompt


def test_tryon_prompt_defaults_to_what_the_mode_asks_for():
    flow, _ = motion_control._tryon_flux_flow("p.png", "o.png", "", "9:16", "product")
    texts = [node["inputs"]["text"] for node in flow.values() if node["class_type"] == "CLIPTextEncode"]
    assert any("Present the product exactly as in image 2." in text for text in texts)


def test_tryon_rejects_unknown_mode(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/tryon", data={"mode": "banana"}, files={
        "video": ("motion.mp4", b"video", "video/mp4"),
        "person": ("person.png", _png(), "image/png"),
        "outfit": ("outfit.png", _png(), "image/png"),
    })
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*"))


def test_tryon_missing_person_job_is_not_found(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/tryon", data={"person_job": "0" * 32}, files={
        "video": ("motion.mp4", b"video", "video/mp4"),
        "outfit": ("outfit.png", _png(), "image/png"),
    })
    assert response.status_code == 404
    assert not list((tmp_path / "motion").glob("*"))


def test_config_reports_flux_readiness_when_offline(client, monkeypatch):
    monkeypatch.setenv("CUTCLIPS_COMFYUI_URL", "http://127.0.0.1:9")
    data = client.get("/api/motion-control/config").json()
    assert data["connected"] is False
    assert data["flux_ready"] is False
    assert data["flux_missing"] == []
    # Lip-sync é template-only: sem CUTCLIPS_LIPSYNC_WORKFLOW ele nem tenta.
    assert data["lipsync_ready"] is False
    assert data["lipsync_missing"] == ["CUTCLIPS_LIPSYNC_WORKFLOW"]


def _lipsync_template():
    return {"1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
            "2": {"class_type": "LoadAudio", "inputs": {"audio": "old.wav"}},
            "3": {"class_type": "SaveVideo", "inputs": {"video": ["4", 0]}}}


def test_lipsync_requires_a_speech_source(client, tmp_path, monkeypatch):
    """Foto sem áudio, sem texto e sem comentários não vira job: falha antes de criar qualquer pasta."""
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/lipsync", files={
        "person": ("person.png", _png(), "image/png"),
    })
    assert response.status_code == 422
    assert "áudio" in response.json()["detail"].lower()
    assert not list((tmp_path / "motion").glob("*"))


def test_lipsync_rejects_too_many_comments(client, tmp_path, monkeypatch):
    """Modo resposta: colar comentários acima do limite falha antes de criar a pasta."""
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/lipsync",
                           data={"reply_to": "comentário " * 400},
                           files={"person": ("person.png", _png(), "image/png")})
    assert response.status_code == 422
    assert "comentários" in response.json()["detail"]
    assert not list((tmp_path / "motion").glob("*"))


def test_lipsync_requires_a_person_source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/lipsync", files={
        "audio": ("fala.mp3", b"audio", "audio/mpeg"),
    })
    assert response.status_code == 422
    assert "influencer" in response.json()["detail"].lower()
    assert not list((tmp_path / "motion").glob("*"))


def test_lipsync_rejects_audio_out_of_range(client, tmp_path, monkeypatch):
    """Áudio longo demais falha na validação, com limpeza da pasta e sem thread."""
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    monkeypatch.setattr(motion_control, "audio_duration", lambda path: 61)
    response = client.post("/api/motion-control/lipsync", files={
        "person": ("person.png", _png(), "image/png"),
        "audio": ("fala.mp3", b"audio", "audio/mpeg"),
    })
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*"))


def test_lipsync_rejects_text_longer_than_one_video(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/lipsync", data={"text": "x" * 1001}, files={
        "person": ("person.png", _png(), "image/png"),
    })
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*"))


def test_lipsync_flow_wires_person_and_speech(tmp_path, monkeypatch):
    path = tmp_path / "lipsync.json"
    path.write_text(json.dumps(_lipsync_template()), encoding="utf-8")
    monkeypatch.setenv("CUTCLIPS_LIPSYNC_WORKFLOW", str(path))
    flow, output = motion_control._lipsync_flow("person.png", "speech.wav")
    assert output == "3"
    assert flow["1"]["inputs"]["image"] == "person.png"
    assert flow["2"]["inputs"]["audio"] == "speech.wav"
    assert flow["3"]["inputs"]["filename_prefix"] == "video/cutclips-lipsync"


def _oneshot_files():
    return {"video": ("motion.mp4", b"video", "video/mp4"),
            "person": ("person.png", _png(), "image/png")}


def test_oneshot_requires_the_lipsync_template(client, tmp_path, monkeypatch):
    """Sem o fluxo de lip-sync não há segundo vídeo: o job nem nasce."""
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    monkeypatch.delenv("CUTCLIPS_LIPSYNC_WORKFLOW", raising=False)
    response = client.post("/api/motion-control/oneshot", data={"product_name": "Sérum Vitamina C"},
                           files=_oneshot_files())
    assert response.status_code == 503
    assert "CUTCLIPS_LIPSYNC_WORKFLOW" in response.json()["detail"]
    assert not list((tmp_path / "motion").glob("*"))


def test_oneshot_requires_the_product_name(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/oneshot", data={"product_name": "ab"}, files=_oneshot_files())
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*"))


def test_oneshot_requires_a_person_source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    response = client.post("/api/motion-control/oneshot", data={"product_name": "Sérum Vitamina C"},
                           files={"video": ("motion.mp4", b"video", "video/mp4")})
    assert response.status_code == 422
    assert "influencer" in response.json()["detail"].lower()
    assert not list((tmp_path / "motion").glob("*"))


def test_oneshot_rejects_long_video(client, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_control, "ROOT", tmp_path / "motion")
    monkeypatch.setattr(motion_control, "probe", lambda path: SimpleNamespace(duration=31))
    template = tmp_path / "lipsync.json"
    template.write_text(json.dumps(_lipsync_template()), encoding="utf-8")
    monkeypatch.setenv("CUTCLIPS_LIPSYNC_WORKFLOW", str(template))
    response = client.post("/api/motion-control/oneshot", data={"product_name": "Sérum Vitamina C"},
                           files=_oneshot_files())
    assert response.status_code == 422
    assert not list((tmp_path / "motion").glob("*"))
