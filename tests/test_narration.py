import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from cutclips import narrate
from cutclips.narrate import Narration
from cutclips.probe import probe


def payload(**over):
    return {"subject": "curiosidades do oceano", "footage": "color", "captions": False, **over}


def test_validation_rejects_what_would_break_the_render():
    assert Narration.model_validate(payload()).orientation == "vertical"
    for bad in ({"subject": "ab"}, {"sentences": 99}, {"orientation": "diagonal"},
                {"background_color": "vermelho"}, {"footage": "youtube"}):
        with pytest.raises(ValidationError):
            Narration.model_validate(payload(**bad))


def test_manual_script_skips_the_ai_entirely(tmp_path, monkeypatch):
    """Roteiro escrito à mão não pode chamar provedor nenhum."""
    monkeypatch.setattr(narrate, "write_script",
                        lambda *a, **k: pytest.fail("não deveria chamar a IA"))
    monkeypatch.setattr(narrate, "render_narration",
                        lambda spec, script, terms, *a, **k: {"kind": "narration", "clips": [{}],
                                                              "script": script, "terms": terms})
    settings = {"narration": payload(script="Primeira frase. Segunda frase. Terceira frase.",
                                     sentences=2)}
    manifest = narrate.process_narration(settings, tmp_path)
    assert manifest["script"] == ["Primeira frase", "Segunda frase"]


def test_pexels_without_a_key_returns_nothing(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert narrate.search_pexels("ocean", 4.0) == []


def test_folder_footage_is_the_fallback_when_pexels_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(narrate, "search_pexels", lambda *a, **k: [])
    monkeypatch.setattr(narrate.backgrounds, "usable", lambda: [{"name": "fundo.mp4"}])
    monkeypatch.setattr(narrate.backgrounds, "background_path", lambda name: tmp_path / name)
    spec = Narration.model_validate(payload(footage="auto"))
    assert narrate.gather_footage(spec, ["ocean"], 20.0, tmp_path) == [tmp_path / "fundo.mp4"]
    # Fundo liso não busca imagem nenhuma.
    plain = Narration.model_validate(payload(footage="color"))
    assert narrate.gather_footage(plain, ["ocean"], 20.0, tmp_path) == []


def test_real_render_produces_a_playable_vertical_video(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg é necessário")
    if not narrate.list_voices():
        pytest.skip("nenhuma voz de sistema instalada")
    spec = Narration.model_validate(payload())
    manifest = narrate.render_narration(spec, ["O oceano cobre setenta por cento do planeta."],
                                        [], tmp_path, width=270, height=480)
    video = tmp_path / "clips" / "narration.mp4"
    info = probe(video)
    assert info.width == 270 and info.height == 480 and info.has_audio
    assert info.duration > 1.0
    assert (tmp_path / "clips" / "narration-cover.jpg").is_file()
    assert manifest["kind"] == "narration" and manifest["clips"][0]["file"] == "narration.mp4"
    print("RENDER_ARTIFACT=" + str(video.resolve()))
