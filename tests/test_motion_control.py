"""Motion Control request validation without remote credits."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest
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


def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / "motion"
    monkeypatch.setattr(motion_control, "ROOT", root)
    # O cancelamento tenta interromper o ComfyUI: aponta para uma porta morta para
    # a chamada falhar rápido e sem rede real.
    monkeypatch.setenv("CUTCLIPS_COMFYUI_URL", "http://127.0.0.1:9")
    return root


def _write_job(root, job_id, **extra):
    folder = root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {"id": job_id, "status": "queued", "created_at": 1.0}
    data.update(extra)
    (folder / "status.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return folder


def test_cancel_marks_the_job_and_only_active_jobs_can_be_cancelled(client, tmp_path, monkeypatch):
    """Cancelar derruba o job para 'cancelled' com a bandeira no lugar; de novo dá 409."""
    root = _isolated_root(tmp_path, monkeypatch)
    monkeypatch.setattr(motion_control, "_run_lipsync", lambda *args: None)
    created = client.post("/api/motion-control/lipsync", data={"text": "Oi, tudo bem?"},
                          files={"person": ("person.png", _png(), "image/png")})
    assert created.status_code == 200
    job_id = created.json()["id"]

    cancelled = client.post(f"/api/motion-control/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert (root / job_id / "cancel.flag").is_file()

    assert client.post(f"/api/motion-control/{job_id}/cancel").status_code == 409
    assert client.post(f"/api/motion-control/{'f' * 32}/cancel").status_code == 404


def test_cancel_is_rejected_for_finished_jobs(client, tmp_path, monkeypatch):
    root = _isolated_root(tmp_path, monkeypatch)
    _write_job(root, "c" * 32, status="done")
    assert client.post(f"/api/motion-control/{'c' * 32}/cancel").status_code == 409
    _write_job(root, "d" * 32, status="error")
    assert client.post(f"/api/motion-control/{'d' * 32}/cancel").status_code == 409


def test_retry_restarts_with_stored_parameters_and_clears_the_cancel_flag(client, tmp_path, monkeypatch):
    """Retry volta a rodar o job com os parâmetros originais, pulando o cancelamento antigo."""
    root = _isolated_root(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(motion_control, "_run_tryon", lambda *args: seen.append(args))
    job_id = "a" * 32
    folder = _write_job(root, job_id, kind="tryon", mode="product", status="cancelled",
                        run_prompt="mostre o perfume", error="Falhou antes")
    (folder / "cancel.flag").touch()

    response = client.post(f"/api/motion-control/{job_id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["error"] == ""
    assert not (folder / "cancel.flag").exists()
    assert seen == [(job_id, "mostre o perfume", "product")]
    # Segundo clique não pode empilhar outra thread em cima da mesma.
    assert client.post(f"/api/motion-control/{job_id}/retry").status_code == 409


def test_retry_reruns_each_kind_with_its_own_runner(client, tmp_path, monkeypatch):
    root = _isolated_root(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(motion_control, "_run_lipsync", lambda *args: calls.append(("lipsync", args)))
    monkeypatch.setattr(motion_control, "_run_oneshot", lambda *args: calls.append(("oneshot", args)))
    monkeypatch.setattr(motion_control, "_run", lambda *args: calls.append(("motion", args)))

    _write_job(root, "e" * 32, kind="lipsync", status="error",
               run_text="Tudo ótimo", voice="Maria", run_reply="")
    client.post(f"/api/motion-control/{'e' * 32}/retry")
    _write_job(root, "f" * 32, kind="oneshot", status="error", mode="product",
               run_prompt="descreva", product_name="Sérum", benefit="b", cta="c", script="s")
    client.post(f"/api/motion-control/{'f' * 32}/retry")
    _write_job(root, "1" * 32, status="error", run_prompt="dance prompt")
    client.post(f"/api/motion-control/{'1' * 32}/retry")

    assert calls[0] == ("lipsync", ("e" * 32, "Tudo ótimo", "Maria", ""))
    assert calls[1] == ("oneshot", ("f" * 32, "descreva", "product", "Sérum", "b", "c", "s"))
    assert calls[2] == ("motion", ("1" * 32, "dance prompt"))


def test_retry_needs_a_failed_job(client, tmp_path, monkeypatch):
    root = _isolated_root(tmp_path, monkeypatch)
    _write_job(root, "2" * 32, kind="lipsync", status="done")
    assert client.post(f"/api/motion-control/{'2' * 32}/retry").status_code == 409
    assert client.post(f"/api/motion-control/{'3' * 32}/retry").status_code == 404


def test_stage_done_only_skips_when_flag_and_file_agree(tmp_path, monkeypatch):
    """Retomada pula a etapa só com bandeira salva E arquivo completo."""
    root = _isolated_root(tmp_path, monkeypatch)
    job_id = "4" * 32
    folder = _write_job(root, job_id, status="error")
    artifact = folder / "result.mp4"
    artifact.write_bytes(b"video")
    assert motion_control._stage_done(job_id, "done_dance", artifact) is False
    (folder / "status.json").write_text(json.dumps({"id": job_id, "done_dance": True}), encoding="utf-8")
    assert motion_control._stage_done(job_id, "done_dance", artifact) is True
    artifact.unlink()
    assert motion_control._stage_done(job_id, "done_dance", artifact) is False


def test_guard_speech_rejects_generated_audio_over_the_limit(tmp_path, monkeypatch):
    """TTS sintetizado é medido antes do ComfyUI: erro em segundos, não depois do render."""
    path = tmp_path / "speech.wav"
    path.write_bytes(b"wav")
    monkeypatch.setattr(motion_control, "audio_duration", lambda p: 74.6)
    with pytest.raises(RuntimeError, match="60"):
        motion_control._guard_speech(path)
    monkeypatch.setattr(motion_control, "audio_duration", lambda p: 12)
    assert motion_control._guard_speech(path) is None
    monkeypatch.setattr(motion_control, "audio_duration", lambda p: 0.4)
    with pytest.raises(RuntimeError):
        motion_control._guard_speech(path)


def test_fail_prefers_a_pending_cancel_and_finish_respects_the_flag(tmp_path, monkeypatch):
    root = _isolated_root(tmp_path, monkeypatch)
    job_id = "5" * 32
    folder = _write_job(root, job_id, status="processing")
    (folder / "cancel.flag").touch()
    motion_control._fail(job_id, RuntimeError("ComfyUI parou"))
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "cancelled"
    (folder / "cancel.flag").unlink()
    motion_control._fail(job_id, RuntimeError("quebrou"))
    assert json.loads((folder / "status.json").read_text(encoding="utf-8"))["error"] == "quebrou"
    motion_control._fail(job_id, subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"boom"))
    assert json.loads((folder / "status.json").read_text(encoding="utf-8"))["error"].startswith("FFmpeg falhou")
    # Conclusão durante um cancelamento vira cancelled, não done.
    (folder / "cancel.flag").touch()
    motion_control._finish(job_id)
    assert json.loads((folder / "status.json").read_text(encoding="utf-8"))["status"] == "cancelled"
    (folder / "cancel.flag").unlink()
    motion_control._finish(job_id, done_dance=True)
    final = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    assert final["status"] == "done"
    assert final["done_dance"] is True
