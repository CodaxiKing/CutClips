"""Motion Control request validation without remote credits."""
import json
from types import SimpleNamespace

from api import motion_control


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
