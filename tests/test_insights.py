import json
import shutil
import subprocess
from dataclasses import replace

import pytest

from cutclips import insights, select
from cutclips.config import Config


def cfg(provider="heuristic"):
    return replace(Config(), llm_provider=provider)


def clip(**extra):
    return {"index": 1, "revision": 0, "file": "missing.mp4", "title": "Título", "text": "", **extra}


def by_category(ins):
    return {item["category"]: item for item in ins["risk"]["items"]}


def test_profanity_respects_word_boundaries():
    assert insights._language_risks("o computador venceu a disputa") == []
    assert insights._language_risks("que merda")[0]["level"] == "medio"
    assert insights._language_risks("merda, porra e caralho")[0]["level"] == "alto"
    assert "(2x)" in insights._language_risks("PORRA porra")[0]["reason"]


def test_local_analysis_marks_what_needs_ai_as_not_analysed(tmp_path):
    manifest = {"clips": [clip(text="uma frase neutra")]}
    insights.annotate(manifest, tmp_path, cfg("heuristic"))
    ins = manifest["clips"][0]["insights"]
    items = by_category(ins)
    assert ins["analysis"] == "local"
    assert items["conteudo"]["level"] is None and items["desinformacao"]["level"] is None
    assert ins["risk"]["level"] == "baixo"
    assert "configure uma IA" in ins["note"]


def test_clip_potential_reuses_the_editorial_score(tmp_path):
    manifest = {"clips": [clip(score=72.4, criteria={"gancho": 4, "retencao": 3, "emocao_utilidade": 5},
                               title_options=["A", "B"])]}
    insights.annotate(manifest, tmp_path, cfg())
    ins = manifest["clips"][0]["insights"]
    assert ins["potential"] == 72
    assert ins["engagement"] == {"gancho": 80, "retencao": 60, "emocao": 100}
    assert ins["titles"][:3] == ["A", "B", "Título"]


def test_top5_audio_and_reuse_follow_declared_rights(tmp_path):
    entries = [{"rights": "own", "audio_mode": "original"},
               {"rights": "unknown", "audio_mode": "original"},
               {"rights": "unlicensed", "audio_mode": "mute"}]
    manifest = {"kind": "top5", "clips": [clip()]}
    insights.annotate(manifest, tmp_path, cfg(), settings={"top5": {"entries": entries}})
    items = by_category(manifest["clips"][0]["insights"])
    assert items["audio"]["level"] == "alto"
    # Só a posição 2 conta: a 1 tem direitos e a 3 está sem som.
    assert len(items["audio"]["reasons"]) == 1 and "Posição 2" in items["audio"]["reasons"][0]
    assert items["reuso"]["level"] == "alto"


def test_top5_with_all_rights_declared_is_not_an_audio_risk(tmp_path):
    entries = [{"rights": "authorized", "audio_mode": "original"}, {"rights": "own", "audio_mode": "original"}]
    manifest = {"kind": "top5", "clips": [clip()]}
    insights.annotate(manifest, tmp_path, cfg(), settings={"top5": {"entries": entries}})
    items = by_category(manifest["clips"][0]["insights"])
    assert "audio" not in items
    assert items["reuso"]["level"] == "medio"


def test_clip_from_a_link_asks_to_confirm_authorization(tmp_path):
    manifest = {"clips": [clip(score=50)]}
    insights.annotate(manifest, tmp_path, cfg(), origin="link")
    assert by_category(manifest["clips"][0]["insights"])["reuso"]["level"] == "medio"


def test_provider_failure_never_breaks_the_manifest(tmp_path, monkeypatch):
    def boom(*_):
        raise RuntimeError("rede fora")
    monkeypatch.setitem(select.PROVIDERS, "anthropic", boom)
    manifest = {"clips": [clip(text="texto")]}
    insights.annotate(manifest, tmp_path, cfg("anthropic"))
    ins = manifest["clips"][0]["insights"]
    assert ins["analysis"] == "local"
    assert "falhou" in ins["note"]


def test_ai_review_is_validated_before_use(tmp_path, monkeypatch):
    answer = {"videos": [{"index": 1,
        "risks": [{"category": "conteudo", "level": "alto", "reason": "Descreve briga",
                   "quote": "ele deu um soco"},
                  {"category": "desinformacao", "level": "medio", "reason": "Cita cura",
                   "quote": "frase que não está no texto"},
                  {"category": "inventada", "level": "alto", "reason": "x"},
                  {"category": "linguagem", "level": "baixo", "reason": "baixo não é alerta"}],
        "engagement": {"gancho": 4, "retencao": 9, "emocao": True},
        "titles": ["Novo título", "título"]}]}
    monkeypatch.setitem(select.PROVIDERS, "anthropic", lambda *_: json.dumps(answer))
    manifest = {"kind": "reaction", "clips": [clip(text="Na hora, ele deu um soco na mesa.", actual_duration=40)]}
    insights.annotate(manifest, tmp_path, cfg("anthropic"), settings={"reaction": {}})
    ins = manifest["clips"][0]["insights"]
    items = by_category(ins)
    assert ins["analysis"] == "ia" and ins["note"] is None
    assert items["conteudo"]["level"] == "alto" and items["conteudo"]["quotes"] == ["ele deu um soco"]
    # Citação inexistente sai; o motivo fica.
    assert items["desinformacao"]["quotes"] == [] and items["desinformacao"]["reasons"] == ["Cita cura"]
    assert "inventada" not in items and "linguagem" not in items
    # Fora da escala e booleano são descartados.
    assert ins["engagement"]["gancho"] == 80 and ins["engagement"]["retencao"] is None
    # Título repetido com outra caixa não duplica.
    assert ins["titles"] == ["Novo título", "título"]
    assert ins["risk"]["level"] == "alto"


def test_clip_left_out_by_the_ai_does_not_blame_the_configuration(tmp_path, monkeypatch):
    monkeypatch.setitem(select.PROVIDERS, "anthropic", lambda *_: json.dumps({"videos": []}))
    manifest = {"clips": [clip(text="texto")]}
    insights.annotate(manifest, tmp_path, cfg("anthropic"))
    reasons = by_category(manifest["clips"][0]["insights"])["conteudo"]["reasons"]
    assert reasons == ["Não analisado: a IA não devolveu avaliação deste vídeo."]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="precisa de FFmpeg")
def test_continuous_sound_without_speech_reads_as_possible_music(tmp_path):
    tone, quiet = tmp_path / "tone.wav", tmp_path / "quiet.wav"
    for path, source in ((tone, "sine=frequency=440:duration=6"), (quiet, "anullsrc=r=16000:cl=mono")):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-t", "6", str(path)], check=True)
    assert insights._music_like_ratio(tone) > .8
    assert insights._music_like_ratio(quiet) == 0
    assert insights._music_like_ratio(tmp_path / "nao-existe.mp4") is None
