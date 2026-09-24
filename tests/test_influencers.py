"""Influencer Studio validation without calling paid ComfyUI partner nodes."""
import io

from PIL import Image

from api import influencers
from api.local_comfy import flux_image_workflow, local_3d_workflow, validate_local_workflow


def _image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (360, 360), "#aabbcc").save(output, format="PNG")
    return output.getvalue()


def test_outfit_requires_clothing_image(client, tmp_path, monkeypatch):
    monkeypatch.setattr(influencers, "ROOT", tmp_path / "influencers")
    response = client.post("/api/influencers", data={"mode": "outfit", "description": "roupa azul"},
                           files={"person": ("person.png", _image(), "image/png")})
    assert response.status_code == 422
    assert not list((tmp_path / "influencers").glob("*/status.json"))


def test_local_image_workflow_uses_flux_and_both_references():
    flow, output = flux_image_workflow("studio portrait", "3:4", "1K", ["person.png", "coat.png"])
    assert output == "20"
    assert flow["1"]["class_type"] == "UNETLoader"
    assert flow["6"]["inputs"]["positive"] == ["36", 0]
    assert flow["6"]["inputs"]["negative"] == ["37", 0]
    assert flow["30"]["inputs"]["image"] == "person.png"
    assert flow["34"]["inputs"]["image"] == "coat.png"
    assert not any("Gemini" in node["class_type"] for node in flow.values())


def test_paid_nodes_are_rejected():
    import pytest
    with pytest.raises(ValueError, match="bloqueado"):
        validate_local_workflow({"1": {"class_type": "KlingMotionControl", "inputs": {}}})


def test_local_3d_template_maps_input_views(tmp_path, monkeypatch):
    import json
    template = {"1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
                "2": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
                "3": {"class_type": "SaveGLB", "inputs": {"mesh": ["4", 0]}}}
    path = tmp_path / "hunyuan.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    monkeypatch.setenv("CUTCLIPS_HUNYUAN3D_WORKFLOW", str(path))
    flow, output = local_3d_workflow({"person": "front.png", "left": "left.png"})
    assert output == "3"
    assert flow["1"]["inputs"]["image"] == "front.png"
    assert flow["2"]["inputs"]["image"] == "left.png"


def test_reference_photos_are_downscaled_for_flux(tmp_path):
    photo = tmp_path / "person.png"
    Image.new("RGB", (4000, 3000), "#aabbcc").save(photo)
    small = tmp_path / "secondary.png"
    Image.new("RGB", (640, 480), "#aabbcc").save(small)
    with Image.open(influencers._reference(photo)) as image:
        assert image.width * image.height <= 1_010_000
        assert abs(image.width / image.height - 4 / 3) < 0.01
    with Image.open(influencers._reference(small)) as image:
        assert image.size == (640, 480)
