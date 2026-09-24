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


def test_voice_rate_stays_within_what_sapi_accepts():
    assert Narration.model_validate(payload(voice_rate=5)).voice_rate == 5
    for bad in (99, -99):
        with pytest.raises(ValidationError):
            Narration.model_validate(payload(voice_rate=bad))


def test_influencer_voice_profile_roundtrip_and_clear(tmp_path, monkeypatch):
    """O perfil é dado de preferência: grava, lido e limpa sem tocar em job nenhum."""
    monkeypatch.setattr(narrate, "VOICE_FILE", tmp_path / "voice.json")
    assert narrate.influencer_voice() == {"voice": "", "rate": 0, "saved_at": None}
    saved = narrate.save_influencer_voice("Microsoft Maria", 3)
    assert saved["voice"] == "Microsoft Maria" and saved["saved_at"]
    profile = narrate.influencer_voice()
    assert profile["voice"] == "Microsoft Maria" and profile["rate"] == 3
    # Vazia = voltar ao padrão do sistema, não guardar um perfil eterno.
    assert narrate.save_influencer_voice("", 9) == {"voice": "", "rate": 0, "saved_at": None}
    assert narrate.influencer_voice() == {"voice": "", "rate": 0, "saved_at": None}


def test_saving_a_voice_the_machine_does_not_have_is_rejected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(narrate, "VOICE_FILE", tmp_path / "voice.json")
    monkeypatch.setattr(narrate, "list_voices", lambda: ["Microsoft Maria", "Microsoft Daniel"])
    bad = client.post("/api/narration/voice", json={"voice": "Inexistente", "rate": 0})
    assert bad.status_code == 422
    good = client.post("/api/narration/voice", json={"voice": "Microsoft Maria", "rate": 2})
    assert good.status_code == 201
    profile = client.get("/api/narration/voice").json()
    assert profile["voice"] == "Microsoft Maria" and profile["rate"] == 2


def test_narration_without_a_voice_falls_back_to_the_influencer_profile(client, tmp_path, monkeypatch):
    """Pedido sem voz não é neutro: é a voz da influencer, aplicada no servidor."""
    from api import db
    monkeypatch.setattr(narrate, "VOICE_FILE", tmp_path / "voice.json")
    monkeypatch.setattr(narrate, "list_voices", lambda: ["Microsoft Maria"])
    client.post("/api/narration/voice", json={"voice": "Microsoft Maria", "rate": 3})
    answer = client.post("/api/narration", json=payload(script="Primeira frase. Segunda frase."))
    assert answer.status_code == 202
    spec = db.get_job(answer.json()["job_id"])["settings"]["narration"]
    assert spec["voice"] == "Microsoft Maria"
    assert spec["voice_rate"] == 3


def test_sell_script_falls_back_to_the_template_without_ai(monkeypatch):
    """Sem provedor pronto o one-shot ainda fala: o template é contrato, não erro."""
    from cutclips import quiz_ai
    monkeypatch.setattr(quiz_ai, "ai_status", lambda cfg=None: {"ready": False})
    lines = narrate.sell_script("Sérum Vitamina C", "acabou com a minha olheira", "pega o link da vitrine")
    assert lines[0] == "Olha só o que chegou: Sérum Vitamina C."
    assert "acabou com a minha olheira" in lines[1]
    assert lines[2] == "pega o link da vitrine"
    plain = narrate.sell_script("Garrafa Térmica")
    assert "surpreendeu" in plain[1]
    assert plain[2] == narrate.SELL_FALLBACK_CTA


def test_sell_script_uses_the_ai_when_a_provider_is_ready(monkeypatch):
    from cutclips import quiz_ai, select
    from cutclips.config import Config
    monkeypatch.setattr(quiz_ai, "ai_status", lambda cfg=None: {"ready": True})
    monkeypatch.setitem(select.PROVIDERS, "anthropic",
                        lambda system, user, cfg: '{"script": ["Gancho com o produto.", "Benefício real.", "Corre lá!"]}')
    lines = narrate.sell_script("Chá Verde", cfg=Config(llm_provider="anthropic"))
    assert lines == ["Gancho com o produto.", "Benefício real.", "Corre lá!"]
