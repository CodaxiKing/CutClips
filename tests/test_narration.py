import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

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


# --------------------------------------------------------------------------- #
# Piper (voz neural local)
# --------------------------------------------------------------------------- #

def _piper_env(tmp_path, monkeypatch):
    binary = tmp_path / "piper.exe"
    binary.write_bytes(b"exe")
    model = tmp_path / "pt_BR-faber-medium.onnx"
    model.write_bytes(b"model")
    monkeypatch.setenv("CUTCLIPS_PIPER_BIN", str(binary))
    monkeypatch.setenv("CUTCLIPS_PIPER_MODEL", str(model))
    monkeypatch.setattr(narrate, "_piper_flags", None)


def _fake_subprocess(calls, fail: str = ""):
    """Grava o wav falso no caminho que o argv pediu, para o SAPI e para o Piper."""
    def run(argv, **kwargs):
        calls.append(argv)
        if "powershell" in argv[0]:
            marker = "SetOutputToWaveFile('"
            if marker not in argv[-1]:
                # list_voices: devolve as vozes instaladas.
                return SimpleNamespace(returncode=0, stdout="Microsoft Maria\n", stderr="")
            target = argv[-1].split(marker, 1)[1].split("')", 1)[0]
            Path(target).write_bytes(b"RIFF" + b"\0" * 4096)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if fail:
            return SimpleNamespace(returncode=1, stdout="", stderr=fail)
        flag = "--output_file" if "--output_file" in argv else "--output-file"
        Path(argv[argv.index(flag) + 1]).write_bytes(b"RIFF" + b"\0" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    # SubprocessError junto: o except do narrate referencia narrate.subprocess.
    return SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError)


def _patch_subprocess(monkeypatch, calls, fail: str = ""):
    monkeypatch.setattr(narrate, "subprocess", _fake_subprocess(calls, fail))


def test_piper_status_reports_whatever_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CUTCLIPS_PIPER_BIN", raising=False)
    monkeypatch.delenv("CUTCLIPS_PIPER_MODEL", raising=False)
    status = narrate.piper_status()
    assert status["ready"] is False
    assert status["missing"] == ["CUTCLIPS_PIPER_BIN", "CUTCLIPS_PIPER_MODEL"]
    _piper_env(tmp_path, monkeypatch)
    status = narrate.piper_status()
    assert status["ready"] is True and status["missing"] == []
    assert status["voice"] == "Piper · pt_BR-faber-medium"


def test_piper_is_listed_first_and_speaks_by_default(tmp_path, monkeypatch):
    """Configurar os env vars é escolher o Piper: primeiro da lista e motor padrão."""
    _piper_env(tmp_path, monkeypatch)
    calls = []
    _patch_subprocess(monkeypatch, calls)
    voices = narrate.list_voices()
    assert voices[0] == "Piper · pt_BR-faber-medium"
    assert "Microsoft Maria" in voices
    out = tmp_path / "fala.wav"
    narrate.speak("Oi, tudo bem?", out)
    assert out.is_file()
    piper_call = next(argv for argv in calls if "--model" in argv)
    assert str(tmp_path / "pt_BR-faber-medium.onnx") in piper_call
    assert "--length_scale" in piper_call and "1.0" in piper_call
    # O texto vai por stdin: nenhum comando pode levá-lo como argumento.
    assert all(all("Oi, tudo bem?" not in arg for arg in argv) for argv in calls)


def test_piper_rate_translates_to_length_scale(tmp_path, monkeypatch):
    _piper_env(tmp_path, monkeypatch)
    calls = []
    _patch_subprocess(monkeypatch, calls)
    narrate.speak("oi", tmp_path / "rapido.wav", "Piper · pt_BR-faber-medium", rate=10)
    fast = next(argv for argv in calls if "--model" in argv)
    assert fast[fast.index("--length_scale") + 1] == "0.6"
    narrate.speak("oi", tmp_path / "devagar.wav", "Piper · pt_BR-faber-medium", rate=-10)
    slow = [argv for argv in calls if "--model" in argv][-1]
    assert slow[slow.index("--length_scale") + 1] == "1.6"
    assert narrate._length_scale(0) == "1.0"


def test_an_explicit_sapi_voice_stays_on_sapi_even_with_piper(tmp_path, monkeypatch):
    """Quem escolheu uma voz do Windows à mão não muda de motor por trás."""
    _piper_env(tmp_path, monkeypatch)
    calls = []
    _patch_subprocess(monkeypatch, calls)
    out = tmp_path / "sapi.wav"
    narrate.speak("oi", out, "Microsoft Maria")
    assert out.is_file()
    assert any("powershell" in argv[0] for argv in calls)
    assert not any("--model" in argv for argv in calls)


def test_piper_failure_raises_with_the_stderr_instead_of_an_empty_wav(tmp_path, monkeypatch):
    _piper_env(tmp_path, monkeypatch)
    calls = []
    _patch_subprocess(monkeypatch, calls, fail="modelo corrompido")
    with pytest.raises(RuntimeError, match="modelo corrompido"):
        narrate.speak("oi", tmp_path / "quebrou.wav", "Piper · pt_BR-faber-medium")


def test_voices_endpoint_defaults_to_piper_and_reports_it(client, tmp_path, monkeypatch):
    _piper_env(tmp_path, monkeypatch)
    monkeypatch.setattr(narrate, "list_voices",
                        lambda: [narrate.piper_status()["voice"], "Microsoft Maria"])
    data = client.get("/api/narration/voices").json()
    assert data["default"] == "Piper · pt_BR-faber-medium"
    assert data["piper"] == {"ready": True, "missing": []}
    assert data["voices"][0].startswith("Piper · ")


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


def test_reply_script_falls_back_to_the_template_without_ai(monkeypatch):
    """Sem provedor pronto o comentário ainda é respondido: agradecer não espera chave."""
    from cutclips import quiz_ai
    monkeypatch.setattr(quiz_ai, "ai_status", lambda cfg=None: {"ready": False})
    assert narrate.reply_script("amei o produto, chegou rápido!") == narrate.REPLY_FALLBACK


def test_reply_script_uses_the_ai_when_a_provider_is_ready(monkeypatch):
    from cutclips import quiz_ai, select
    from cutclips.config import Config
    monkeypatch.setattr(quiz_ai, "ai_status", lambda cfg=None: {"ready": True})
    monkeypatch.setitem(select.PROVIDERS, "anthropic",
                        lambda system, user, cfg: '{"script": ["Valeu!", "Fico feliz que curtiu.", "Comenta mais!"]}')
    lines = narrate.reply_script("top demais", cfg=Config(llm_provider="anthropic"))
    assert lines == ["Valeu!", "Fico feliz que curtiu.", "Comenta mais!"]
