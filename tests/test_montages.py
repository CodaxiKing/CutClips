import json
import random
import re
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import db, main, studio, worker
from cutclips import backgrounds, insights, montage, quiz_ai, quiz_bank
from cutclips import quiz as qz
from cutclips import reaction as rc
from cutclips.config import Config
from cutclips.montage import ffmpeg
from cutclips.probe import probe
from cutclips.select import ProviderError

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
TT = "https://www.tiktok.com/@test/video/7000000000000000001"


def quiz_payload(**update):
    return {"headline": "Você acerta as 3?", "questions": [
        {"question": f"Pergunta {i + 1}?", "answer": f"Resposta {i + 1}"} for i in range(3)], **update}


def choices_payload(**update):
    items = [{"question": f"Pergunta {i + 1}?", "answer": f"Certa {i + 1}",
              "wrong": [f"Errada {i + 1}a", f"Errada {i + 1}b", f"Errada {i + 1}c"]} for i in range(4)]
    return quiz_payload(style="choices", questions=items, **update)


def reaction_payload(**update):
    return {"headline": "Reagindo", "top": {"url": TT, "label": "Original"},
            "bottom": {"url": YT, "label": "Eu"}, **update}


@pytest.fixture(autouse=True)
def isolated_caches(tmp_path, monkeypatch):
    # Sons sintetizados vão para a pasta do teste, nunca para o storage do projeto.
    from cutclips import sounds
    monkeypatch.setattr(sounds, 'SOUND_DIR', tmp_path / 'sons')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(main, 'JOBS', tmp_path / 'jobs')
    monkeypatch.setattr(studio, 'JOBS', tmp_path / 'jobs')
    monkeypatch.setattr(worker, 'STORAGE', tmp_path)
    monkeypatch.setattr(montage, 'MUSIC_DIR', tmp_path / 'music')
    monkeypatch.setattr(backgrounds, 'BACKGROUND_DIR', tmp_path / 'fundos')
    monkeypatch.setattr(backgrounds, 'THUMB_DIR', tmp_path / 'thumbs')
    with TestClient(main.app) as connection:
        yield connection


def _sources(tmp_path):
    a, b = tmp_path / 'landscape.mp4', tmp_path / 'portrait.mp4'
    ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=2.5',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=2.5',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(a)], tmp_path)
    ffmpeg(['-f', 'lavfi', '-i', 'color=c=blue:size=180x320:rate=30:duration=2',
            '-c:v', 'libx264', '-preset', 'ultrafast', str(b)], tmp_path)
    return a, b


# --------------------------------------------------------------------------- #
# Quiz básico, banco e fila
# --------------------------------------------------------------------------- #

def test_quiz_validation():
    assert qz.Quiz.model_validate(quiz_payload()).total == 15
    for update in [{"questions": quiz_payload()["questions"][:2]}, {"headline": "  "},
                   {"suspense": 0.5}, {"background_url": "https://evil.example/video.mp4"},
                   {"background_color": "red"}, {"music": "../../segredo.mp3"}]:
        with pytest.raises(ValidationError):
            qz.Quiz.model_validate(quiz_payload(**update))
    blank = quiz_payload()
    blank["questions"][1]["answer"] = "   "
    with pytest.raises(ValidationError):
        qz.Quiz.model_validate(blank)


def test_reaction_validation():
    assert rc.Reaction.model_validate(reaction_payload())
    for update in [{"top": {"url": "https://www.tiktok.com/@perfil"}},
                   {"bottom": {"url": YT, "label": "duas\nlinhas"}},
                   {"bottom": {"url": YT, "volume": 3}}, {"fit": "stretch"},
                   {"layout": "diagonal"}, {"captions": "both"}]:
        with pytest.raises(ValidationError):
            rc.Reaction.model_validate(reaction_payload(**update))


def test_bank_draws_without_repeating_and_respects_exclude():
    rng = random.Random(7)
    drawn = quiz_bank.suggest("brasil", 5, rng=rng)
    assert len({q["question"] for q in drawn}) == 5
    theme = [item[0] for item in quiz_bank.THEMES["brasil"]["questions"]]
    left = quiz_bank.suggest("brasil", 1, exclude=set(theme[:-1]), rng=rng)
    assert left[0]["question"] == theme[-1]
    # Todo item do banco precisa passar na validação do formato A/B/C/D, senão o
    # sorteio produz um quiz que o formulário recusa.
    for data in quiz_bank.THEMES.values():
        items = [{"question": q, "answer": a, "wrong": list(w)} for q, a, w in data["questions"]]
        for start in range(0, len(items), 10):
            qz.Quiz.model_validate(quiz_payload(style="choices", questions=(items[start:start + 10] + items)[:10]))
    with pytest.raises(KeyError):
        quiz_bank.suggest("inexistente")


def test_api_queues_single_stage_and_serves_bank(client, monkeypatch):
    quiz_id = client.post('/api/quiz', json=quiz_payload()).json()['job_id']
    reaction_id = client.post('/api/reaction', json=reaction_payload()).json()['job_id']
    with db.connect() as c:
        stages = {r['job_id']: r['stage'] for r in c.execute('SELECT job_id,stage FROM pipeline_stages')}
    assert stages == {quiz_id: 'quiz', reaction_id: 'reaction'}
    assert client.post('/api/quiz', json=quiz_payload(questions=[])).status_code == 422

    themes = client.get('/api/quiz/themes').json()['themes']
    assert {t['id'] for t in themes} >= {'geral', 'brasil'}
    got = client.get('/api/quiz/suggestions', params={'theme': 'ciencia', 'count': 4}).json()['questions']
    assert len(got) == 4 and all(len(q['wrong']) == 3 for q in got)
    assert client.get('/api/quiz/suggestions', params={'theme': 'nada'}).status_code == 404
    assert client.get('/assets/montages.js').status_code == 200

    monkeypatch.setattr(qz, 'process_quiz', lambda s, d, p, **hooks: {'kind': 'quiz', 'clips': []})
    monkeypatch.setattr(rc, 'process_reaction', lambda s, d, p: {'kind': 'reaction', 'clips': []})
    monkeypatch.setattr(worker, 'transcribe', lambda *a, **k: pytest.fail('montagem não transcreve'))
    for _ in range(2):
        task = db.claim_pipeline_stage()
        assert not db.finish_montage({**task, 'token': 'stale'}, {'clips': []})
        worker.run_pipeline_stage(task)
    assert db.get_job(quiz_id)['status'] == 'done' and db.get_job(reaction_id)['status'] == 'done'
    assert db.get_job(reaction_id)['manifest']['kind'] == 'reaction'


def test_quiz_script_reveals_after_suspense_and_escapes(tmp_path):
    value = quiz_payload(suspense=2.5, reveal=2)
    value["questions"][2]["answer"] = r"{\pos(0,0)}injetado"
    spec = qz.Quiz.model_validate(value)
    text = qz.quiz_ass(spec, tmp_path / 'quiz.ass').read_text(encoding='utf-8')
    answers = [line for line in text.splitlines() if ',Answer,' in line]
    # Resposta k entra em k*4,5 + 2,5 e sai no fim do bloco, nunca antes.
    assert [a.split(',')[1:3] for a in answers] == [
        ['0:00:02.50', '0:00:04.50'], ['0:00:07.00', '0:00:09.00'], ['0:00:11.50', '0:00:13.50']]
    ticks = [re.search(r'\}(\d+)$', line).group(1) for line in text.splitlines() if ',Tick,' in line][:2]
    assert ticks == ['2', '1']
    assert r'{\pos(0,0)}injetado' not in text
    silent = qz.quiz_ass(qz.Quiz.model_validate(quiz_payload(countdown=False)), tmp_path / 'q2.ass')
    assert ',Tick,' not in silent.read_text(encoding='utf-8')


def test_real_ffmpeg_quiz_without_background(tmp_path):
    spec = qz.Quiz.model_validate(quiz_payload(suspense=1, reveal=1))
    result = qz.render_quiz(spec, None, tmp_path, width=270, height=480)
    info = probe(tmp_path / 'clips/quiz.mp4')
    assert (info.width, info.height, info.fps) == (270, 480, 30)
    assert abs(info.duration - 6) < .15
    assert result['clips'][0]['warnings']  # sem fundo e sem música, avisa que falta som
    assert (tmp_path / 'clips/quiz-cover.jpg').is_file()


# --------------------------------------------------------------------------- #
# Alternativas A/B/C/D
# --------------------------------------------------------------------------- #

def test_choices_validation():
    assert qz.Quiz.model_validate(choices_payload())
    for mutate in [lambda q: q.update(wrong=q["wrong"][:2]),
                   lambda q: q.update(wrong=[q["answer"], "x", "y"]),
                   lambda q: q.update(answer="a" * 33)]:
        value = choices_payload()
        mutate(value["questions"][1])
        with pytest.raises(ValidationError, match="Pergunta 2"):
            qz.Quiz.model_validate(value)


def test_choices_arrangement_is_stable_and_spread():
    spec = qz.Quiz.model_validate(choices_payload())
    first = qz.arrange(spec)
    assert first == qz.arrange(qz.Quiz.model_validate(choices_payload()))
    assert sorted(slot for _, slot in first) == [0, 1, 2, 3]  # quatro perguntas, quatro letras
    for (options, slot), q in zip(first, spec.questions):
        assert options[slot] == q.answer and sorted(options) == sorted([q.answer, *q.wrong])
    assert qz.answer_key(spec).splitlines()[0].endswith(f"{qz.LETTERS[first[0][1]]}) Certa 1")


def test_choices_script_lights_the_right_card(tmp_path):
    spec = qz.Quiz.model_validate(choices_payload(suspense=2, reveal=1))
    text = qz.quiz_ass(spec, tmp_path / 'q.ass').read_text(encoding='utf-8')
    first_slot = qz.arrange(spec)[0][1]
    green = [line for line in text.splitlines() if '&H0040B040&' in line]
    assert len(green) == 4  # um cartão verde por pergunta
    assert green[0].split(',')[1:3] == ['0:00:02.00', '0:00:03.00']
    assert f"\\pos(540,{700 + first_slot * 160 + 65})" in green[0]  # centro do cartão certo
    assert ',Answer,' not in text


def test_real_ffmpeg_choices_with_music(tmp_path, monkeypatch):
    monkeypatch.setattr(montage, 'MUSIC_DIR', tmp_path / 'music')
    (tmp_path / 'music').mkdir()
    name = 'a' * 32 + '.wav'
    ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=44100:duration=1.5',
            str(tmp_path / 'music' / name)], tmp_path)
    assert montage.audio_duration(tmp_path / 'music' / name) > 1
    spec = qz.Quiz.model_validate(choices_payload(suspense=1, reveal=1, music=name))
    result = qz.render_quiz(spec, None, tmp_path, width=270, height=480)
    info = probe(tmp_path / 'clips/quiz.mp4')
    assert info.has_audio and abs(info.duration - 8) < .15
    assert result['music'] and not result['clips'][0]['warnings']
    assert [q['correct'] for q in result['questions']] == [qz.LETTERS[s] for _, s in qz.arrange(spec)]
    # A música de 1,5s repetiu: ainda há som perto do fim de um vídeo de 8s.
    tail = subprocess.run(['ffmpeg', '-hide_banner', '-ss', '5.5', '-t', '1', '-i', str(tmp_path / 'clips/quiz.mp4'),
                           '-af', 'volumedetect', '-vn', '-f', 'null', '-'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace').stderr
    assert float(re.search(r'max_volume: (-?[\d.]+) dB', tail).group(1)) > -40


def test_music_upload_rejects_non_audio(client, tmp_path):
    assert client.post('/api/music', files={'file': ('x.exe', b'MZ')}).status_code == 400
    bad = client.post('/api/music', files={'file': ('fake.mp3', b'not audio at all')})
    assert bad.status_code == 422 and not list((tmp_path / 'music').iterdir())
    ffmpeg(['-f', 'lavfi', '-i', 'sine=duration=2', str(tmp_path / 'tone.mp3')], tmp_path)
    ok = client.post('/api/music', files={'file': ('tone.mp3', (tmp_path / 'tone.mp3').read_bytes())}).json()
    assert re.fullmatch(montage.MUSIC_NAME, ok['music']) and ok['duration'] > 1.5
    assert client.post('/api/quiz', json=quiz_payload(music='b' * 32 + '.mp3')).status_code == 422
    assert client.post('/api/quiz', json=quiz_payload(music=ok['music'])).status_code == 202


# --------------------------------------------------------------------------- #
# Reação: empilhada, lado a lado e legenda
# --------------------------------------------------------------------------- #

def test_real_ffmpeg_reaction_mixed_sources(tmp_path):
    a, b = _sources(tmp_path)
    spec = rc.Reaction.model_validate(reaction_payload(bottom={"url": YT, "label": "Eu", "start": .5}))
    result = rc.render_reaction(spec, {'top': a, 'bottom': b}, tmp_path, width=270, height=480)
    info = probe(tmp_path / 'clips/reaction.mp4')
    assert info.has_audio and (info.width, info.height, info.fps) == (270, 480, 30)
    # O mais curto (fundo azul a partir de 0,5s) define a duração.
    assert abs(info.duration - 1.5) < .1 and abs(result['source_duration'] - 1.5) < .05

    with pytest.raises(ValueError, match='baixo'):
        rc.render_reaction(rc.Reaction.model_validate(reaction_payload(bottom={"url": YT, "start": 1.5})),
                           {'top': a, 'bottom': b}, tmp_path, width=270, height=480)


def test_real_ffmpeg_side_by_side_with_captions(tmp_path, monkeypatch):
    from cutclips.transcribe import Transcript, Word
    a, b = _sources(tmp_path)
    heard = []

    def fake_transcribe(clip, cache):
        heard.append(clip.name)
        return Transcript("pt", 2, [Word(.1, .5, "antes"), Word(.6, 1.0, "e"), Word(1.1, 1.6, "depois")])

    monkeypatch.setattr(rc, 'transcribe_side', fake_transcribe)
    spec = rc.Reaction.model_validate(reaction_payload(
        layout="side", captions="bottom", top={"url": TT, "label": "Antes"}, bottom={"url": YT, "label": "Depois"}))
    result = rc.render_reaction(spec, {'top': a, 'bottom': b}, tmp_path, width=272, height=480)
    info = probe(tmp_path / 'clips/reaction.mp4')
    assert (info.width, info.height) == (272, 480) and info.has_audio
    assert probe(tmp_path / 'reaction-work/top.mov').width == 136  # cada coluna tem metade da largura
    assert heard == ['bottom.mov']  # só a metade escolhida é transcrita
    assert 'depois' in (tmp_path / 'reaction-work/speech.ass').read_text(encoding='utf-8')
    labels = (tmp_path / 'reaction-work/labels.ass').read_text(encoding='utf-8')
    assert 'ANTES' in labels and 'DEPOIS' in labels
    assert result['layout'] == 'side' and result['captions'] == 'bottom'
    # O divisor branco fica no meio do quadro: alguma coluna entre 134 e 137 é clara
    # de cima a baixo, enquanto a borda do quadro não é.
    pixels = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', '1', '-i',
                             str(tmp_path / 'clips/reaction.mp4'), '-vf', 'crop=4:40:134:300',
                             '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True).stdout
    columns = [sum(pixels[col::4]) / 40 for col in range(4)]
    assert max(columns) > 180


def test_captions_without_speech_only_warn(tmp_path, monkeypatch):
    from cutclips.transcribe import Transcript
    a, b = _sources(tmp_path)
    monkeypatch.setattr(rc, 'transcribe_side', lambda clip, cache: Transcript("pt", 2, []))
    spec = rc.Reaction.model_validate(reaction_payload(captions="top"))
    result = rc.render_reaction(spec, {'top': a, 'bottom': b}, tmp_path, width=270, height=480)
    assert any('nenhuma fala' in w for w in result['clips'][0]['warnings'])
    assert not (tmp_path / 'reaction-work/speech.ass').exists()


# --------------------------------------------------------------------------- #
# IA e lote
# --------------------------------------------------------------------------- #

def fake_llm(generated, review=None):
    """Provedor falso: devolve lotes de perguntas em ordem e aprova tudo na revisão."""
    calls = []

    def provider(system, user, cfg):
        # A revisão de publicação roda no fim de cada vídeo; não é pedido de perguntas.
        if system == insights.REVIEW_SYSTEM:
            return '{"videos": []}'
        calls.append((system, user))
        if system == quiz_ai.REVIEW_SYSTEM:
            count = user.count('Pergunta:')
            results = review(count) if review else [
                {"index": i, "correct": True, "unambiguous": True, "wrong_ok": True} for i in range(1, count + 1)]
            return json.dumps({"results": results})
        return "```json\n" + json.dumps({"questions": generated.pop(0)}) + "\n```"
    return provider, calls


def ai_items(prefix, n):
    return [{"question": f"{prefix} {i}?", "answer": f"Sim {i}", "wrong": [f"Não {i}", f"Talvez {i}", f"Nunca {i}"]}
            for i in range(n)]


@pytest.fixture
def ai_ready(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test')
    monkeypatch.setattr(quiz_ai, 'Config', lambda: Config(llm_provider='anthropic'))


def test_ai_generation_filters_reviews_and_avoids(ai_ready, monkeypatch):
    items = ai_items('Q', 6)
    items[1]["wrong"] = ["Sim 1", "x", "y"]  # alternativa igual à certa: cai na validação
    items.append({"question": "Q 0?", "answer": "Sim", "wrong": ["a", "b", "c"]})  # repetida

    def reject_second(n):
        return [{"index": i, "correct": i != 2, "unambiguous": True, "wrong_ok": True} for i in range(1, n + 1)]

    provider, calls = fake_llm([items], reject_second)
    monkeypatch.setitem(quiz_ai.PROVIDERS, 'anthropic', provider)
    result = quiz_ai.generate_questions("espaço", 3, style="choices", avoid=["Q 5?"])
    # Q1 inválida, Q2 reprovada na revisão, Q5 já usada no lote.
    assert [q["question"] for q in result["questions"]] == ["Q 0?", "Q 3?", "Q 4?"]
    assert "Q 5?" in calls[0][1]
    assert len(calls) == 2 and not result["warnings"]


def test_ai_generation_second_round_and_review_failure(ai_ready, monkeypatch):
    state = {"review": 0}
    provider, calls = fake_llm([ai_items('A', 2), ai_items('B', 4)])

    def flaky(system, user, cfg):
        if system == quiz_ai.REVIEW_SYSTEM:
            state["review"] += 1
            if state["review"] == 2:
                raise TimeoutError("lento")
        return provider(system, user, cfg)

    monkeypatch.setitem(quiz_ai.PROVIDERS, 'anthropic', flaky)
    result = quiz_ai.generate_questions("história", 4)
    assert len(result["questions"]) == 4
    assert any('revisão automática falhou' in w for w in result["warnings"])


def test_ai_status_and_errors(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    status = quiz_ai.ai_status(Config(llm_provider='anthropic'))
    assert not status['ready'] and 'ANTHROPIC_API_KEY' in status['message']
    assert not quiz_ai.ai_status(Config(llm_provider='heuristic'))['ready']
    with pytest.raises(ProviderError, match='ANTHROPIC_API_KEY'):
        quiz_ai.generate_questions("x y", 3, cfg=Config(llm_provider='anthropic'))


def test_generate_endpoint_maps_provider_errors(client, ai_ready, monkeypatch):
    provider, _ = fake_llm([[], []])
    monkeypatch.setitem(quiz_ai.PROVIDERS, 'anthropic', provider)
    response = client.post('/api/quiz/generate', json={"topic": "vulcões", "count": 3})
    assert response.status_code == 503 and 'confiáveis' in response.json()['detail']
    provider, _ = fake_llm([ai_items('V', 5)])
    monkeypatch.setitem(quiz_ai.PROVIDERS, 'anthropic', provider)
    ok = client.post('/api/quiz/generate', json={"topic": "vulcões", "count": 3, "style": "choices"}).json()
    assert len(ok['questions']) == 3 and ok['provider'] == 'anthropic'
    assert client.get('/api/quiz/ai').json()['ready']


def test_bank_batch_spreads_questions_and_dates(client):
    body = {"base": {"style": "choices", "suspense": 2, "reveal": 1}, "headline": "Quiz Brasil {n}",
            "videos": 3, "per_video": 4, "source": "bank", "theme": "brasil",
            "start_date": "2026-09-20", "every_days": 2}
    created = client.post('/api/quiz/batch', json=body).json()
    assert len(created['jobs']) == 3
    jobs = [db.get_job(j) for j in created['jobs']]
    used = [q['question'] for job in jobs for q in job['settings']['quiz']['questions']]
    assert len(used) == 12 and len(set(used)) == 12
    assert [job['title'] for job in jobs] == ['Quiz Brasil 1', 'Quiz Brasil 2', 'Quiz Brasil 3']
    assert [job['settings']['batch']['publish_on'] for job in jobs] == ['2026-09-20', '2026-09-22', '2026-09-24']
    for job in jobs:  # cada projeto do lote é um quiz válido
        qz.Quiz.model_validate(job['settings']['quiz'])
    listing = client.get('/api/quiz/batches').json()['batches']
    assert len(listing) == 1 and [i['index'] for i in listing[0]['items']] == [1, 2, 3]

    too_many = client.post('/api/quiz/batch', json={**body, "videos": 4})
    assert too_many.status_code == 422 and '12 perguntas' in too_many.json()['detail']
    assert client.post('/api/quiz/batch', json={**body, "base": {"suspense": 99}}).status_code == 422
    assert len(client.get('/api/quiz/batches').json()['batches']) == 1  # nada criado pela metade


def test_ai_batch_generates_in_worker_without_repeating(client, ai_ready, monkeypatch):
    body = {"base": {"style": "open"}, "headline": "Espaço", "videos": 2, "per_video": 3, "source": "ai",
            "topic": "sistema solar", "start_date": "2026-10-01"}
    created = client.post('/api/quiz/batch', json=body).json()
    assert db.get_job(created['jobs'][0])['settings']['quiz']['questions'] == []

    provider, calls = fake_llm([ai_items('Primeiro', 5), ai_items('Primeiro', 2) + ai_items('Segundo', 4)])
    monkeypatch.setitem(quiz_ai.PROVIDERS, 'anthropic', provider)
    monkeypatch.setattr(qz, 'render_quiz', lambda spec, bg, d, p, **kw: {
        'kind': 'quiz', 'questions': [q.model_dump() for q in spec.questions], 'clips': [{'warnings': []}]})
    for _ in range(2):
        worker.run_pipeline_stage(db.claim_pipeline_stage())
    first, second = (db.get_job(j) for j in created['jobs'])
    assert first['status'] == second['status'] == 'done', (first['error'], second['error'])
    one = {q['question'] for q in first['settings']['quiz']['questions']}
    two = {q['question'] for q in second['settings']['quiz']['questions']}
    assert len(one) == len(two) == 3 and not one & two
    assert 'Primeiro 0?' in calls[2][1]  # o pedido do segundo vídeo levou as perguntas do primeiro
    assert second['manifest']['batch']['index'] == 2 and second['title'] == 'Espaço #2'
    assert 'geradas por IA' in second['manifest']['clips'][0]['warnings'][0]


def test_ai_batch_refused_without_key(client, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(quiz_ai, 'Config', lambda: Config(llm_provider='anthropic'))
    response = client.post('/api/quiz/batch', json={"headline": "X", "videos": 2, "source": "ai", "topic": "abc",
                                                    "start_date": "2026-10-01"})
    assert response.status_code == 503 and not db.batch_jobs()


# --------------------------------------------------------------------------- #
# Pasta de fundos
# --------------------------------------------------------------------------- #

def _clip(path, seconds, colour="red", size="180x320"):
    ffmpeg(['-f', 'lavfi', '-i', f'color=c={colour}:size={size}:rate=30:duration={seconds}',
            '-f', 'lavfi', '-i', f'sine=frequency=500:duration={seconds}',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(path)], path.parent)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(backgrounds, 'BACKGROUND_DIR', tmp_path / 'fundos')
    monkeypatch.setattr(backgrounds, 'THUMB_DIR', tmp_path / 'thumbs')
    return backgrounds.ensure_folder()


def test_old_projects_keep_their_background_mode():
    assert qz.Quiz.model_validate(quiz_payload()).background == "color"
    linked = qz.Quiz.model_validate(quiz_payload(background_url=YT))
    assert linked.background == "url" and linked.background_audio is True
    from_folder = qz.Quiz.model_validate(quiz_payload(background="folder"))
    assert from_folder.background_audio is False  # loop com som dá tranco
    for bad in [{"background": "url"}, {"background": "folder", "background_file": "../x.mp4"}]:
        with pytest.raises(ValidationError):
            qz.Quiz.model_validate(quiz_payload(**bad))


def test_folder_listing_rejects_escapes_and_marks_broken_files(folder):
    _clip(folder / 'b.mp4', 1.5)
    _clip(folder / 'a.mov', 1.5, 'blue')
    (folder / 'quebrado.mp4').write_bytes(b'nada')
    (folder / 'notas.txt').write_text('x')
    listing = backgrounds.list_backgrounds()
    assert [v['name'] for v in listing] == ['a.mov', 'b.mp4', 'quebrado.mp4']
    assert listing[2]['error'] and not listing[0]['error']
    assert [v['name'] for v in backgrounds.usable()] == ['a.mov', 'b.mp4']
    assert (folder / 'LEIA-ME.txt').is_file()
    (folder.parent / 'fora.mp4').write_bytes(b'x')
    for name in ['../fora.mp4', 'notas.txt', 'sumiu.mp4', '.oculto.mp4']:
        with pytest.raises(ValueError):
            backgrounds.background_path(name)
    thumb = backgrounds.thumbnail('a.mov')
    assert thumb.is_file() and backgrounds.thumbnail('a.mov') == thumb  # cache


def test_pick_rotates_through_least_recently_used(folder):
    for name in ['a.mp4', 'b.mp4', 'c.mp4']:
        _clip(folder / name, 1.2)
    rng = random.Random(3)
    used = []
    for _ in range(6):
        used.insert(0, backgrounds.pick_background(used, rng)['name'])
    newest_first = used
    assert sorted(newest_first[:3]) == ['a.mp4', 'b.mp4', 'c.mp4']  # três quizzes, três fundos
    assert newest_first[:3] == newest_first[3:]  # e depois repete na mesma ordem de uso
    assert backgrounds.random_start(10, 4, random.Random(1)) <= 6
    assert backgrounds.random_start(4.5, 4) == 0


def test_process_quiz_picks_from_folder_once_and_loops(tmp_path, folder):
    # Meio segundo vermelho, meio segundo azul: em loop, o segundo 5,2 volta a ser
    # vermelho; congelado no último quadro, seria azul.
    ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:size=320x180:rate=30:duration=0.5',
            '-f', 'lavfi', '-i', 'color=c=blue:size=320x180:rate=30:duration=0.5',
            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0', '-c:v', 'libx264', '-preset', 'ultrafast',
            str(folder / 'curto.mp4')], folder)
    saved = []
    settings = {"kind": "quiz", "quiz": quiz_payload(background="folder", suspense=1, reveal=1)}
    manifest = qz.process_quiz(settings, tmp_path / 'job', save_settings=saved.append,
                               recent_backgrounds=lambda: [])
    assert saved[-1]['background_pick'] == {"file": "curto.mp4", "start": 0.0}
    assert manifest['background'] == {"mode": "folder", "file": "curto.mp4", "start": 0.0}
    info = probe(tmp_path / 'job/clips/quiz.mp4')
    assert abs(info.duration - 6) < .15  # fundo de 1s cobriu um quiz de 6s

    def rgb(at):
        return subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(at), '-i',
                               str(tmp_path / 'job/quiz-work/background.mov'), '-vf', 'crop=10:10:5:5',
                               '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                              capture_output=True).stdout[:3]
    red, blue = rgb(5.2), rgb(5.7)
    assert red[0] > 150 and red[2] < 100, red
    assert blue[2] > 150 and blue[0] < 100, blue
    # O fundo da pasta vem mudo por padrão: sem música, o aviso de som aparece.
    assert manifest['clips'][0]['warnings']

    # Nova tentativa do mesmo projeto reaproveita a escolha gravada.
    again = qz.process_quiz(saved[-1], tmp_path / 'job2', save_settings=saved.append,
                            recent_backgrounds=lambda: pytest.fail('não deveria sortear de novo'))
    assert again['background']['file'] == 'curto.mp4' and len(saved) == 1


def test_background_api(client, tmp_path):
    folder = tmp_path / 'fundos'
    listing = client.get('/api/quiz/backgrounds').json()
    assert listing['videos'] == [] and listing['folder'].endswith('fundos') and folder.is_dir()
    empty = client.post('/api/quiz', json=quiz_payload(background="folder"))
    assert empty.status_code == 422 and 'vazia' in empty.json()['detail']
    _clip(folder / 'praia.mp4', 1.5)
    assert client.post('/api/quiz', json=quiz_payload(background="folder")).status_code == 202
    assert client.post('/api/quiz', json=quiz_payload(background="folder", background_file="outro.mp4")).status_code == 422
    thumb = client.get('/api/quiz/backgrounds/praia.mp4/thumbnail')
    assert thumb.status_code == 200 and thumb.headers['content-type'] == 'image/jpeg'
    assert client.get('/api/quiz/backgrounds/..%2F..%2Ftest.db/thumbnail').status_code == 404
    batch = client.post('/api/quiz/batch', json={"base": {"background": "folder"}, "headline": "Q", "videos": 2,
                                                 "per_video": 3, "source": "bank", "start_date": "2026-10-01"})
    assert batch.status_code == 202


def test_worker_spreads_folder_backgrounds_across_quizzes(client, tmp_path, monkeypatch):
    folder = tmp_path / 'fundos'
    folder.mkdir()
    for name in ['a.mp4', 'b.mp4']:
        _clip(folder / name, 1.2)
    ids = [client.post('/api/quiz', json=quiz_payload(background="folder")).json()['job_id'] for _ in range(4)]
    monkeypatch.setattr(qz, 'render_quiz', lambda spec, bg, d, p, **kw: {
        'kind': 'quiz', 'background': {'file': bg.name}, 'clips': [{'warnings': []}]})
    for _ in ids:
        worker.run_pipeline_stage(db.claim_pipeline_stage())
    used = [db.get_job(i)['manifest']['background']['file'] for i in ids]
    assert sorted(used[:2]) == ['a.mp4', 'b.mp4'] and used[2:] == used[:2]


def test_suspense_sets_where_the_countdown_starts(tmp_path):
    for suspense, expected in [(5, ['5', '4', '3', '2', '1']), (15, [str(n) for n in range(15, 0, -1)]),
                               (1, ['1']), (3.5, ['3', '2', '1'])]:
        spec = qz.Quiz.model_validate(quiz_payload(suspense=suspense))
        text = qz.quiz_ass(spec, tmp_path / 'q.ass').read_text(encoding='utf-8')
        first_block = [line for line in text.splitlines() if ',Tick,' in line][:len(expected)]
        assert [re.search(r'\}(\d+)$', line).group(1) for line in first_block] == expected
        answer = next(line for line in text.splitlines() if ',Answer,' in line)
        assert answer.split(',')[1] == qz._ts(suspense)  # a resposta entra quando a contagem acaba
    # Com 3,5s o primeiro número segura 1,5s: "3" de 0 a 1,5.
    spec = qz.Quiz.model_validate(quiz_payload(suspense=3.5))
    ticks = [l for l in qz.quiz_ass(spec, tmp_path / 'q.ass').read_text(encoding='utf-8').splitlines() if ',Tick,' in l]
    assert ticks[0].split(',')[1:3] == ['0:00:00.00', '0:00:01.50']
    assert ticks[1].split(',')[1:3] == ['0:00:01.50', '0:00:02.50']
    with pytest.raises(ValidationError):
        qz.Quiz.model_validate(quiz_payload(suspense=16))


def test_caches_work_with_relative_storage(tmp_path, monkeypatch):
    # O .env padrão usa CUTCLIPS_STORAGE=./storage: caminho relativo não pode
    # quebrar quando o ffmpeg roda dentro da pasta de cache.
    from pathlib import Path
    from cutclips import sounds
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sounds, 'SOUND_DIR', Path('storage/cache/sons'))
    monkeypatch.setattr(backgrounds, 'BACKGROUND_DIR', Path('storage/fundos-quiz'))
    monkeypatch.setattr(backgrounds, 'THUMB_DIR', Path('storage/cache/fundos-quiz'))
    assert sounds.preset_path('pop').is_file()
    backgrounds.ensure_folder()
    _clip(tmp_path / 'storage/fundos-quiz/x.mp4', 1.2)
    assert backgrounds.thumbnail('x.mp4').is_file()


def test_reveal_script_animates_and_can_be_turned_off(tmp_path):
    spec = qz.Quiz.model_validate(quiz_payload(suspense=2, reveal=2))
    text = qz.quiz_ass(spec, tmp_path / 'q.ass').read_text(encoding='utf-8')
    answer = next(line for line in text.splitlines() if ',Answer,' in line)
    assert r'\t(0,170,\fscx118' in answer  # a resposta dá um "pop"
    confetti = [line for line in text.splitlines() if r'\move(' in line]
    assert len(confetti) == qz.CONFETTI_PIECES * 3 and confetti[0].split(',')[1] == '0:00:02.00'
    # Mesma semente: renderizar de novo desenha o mesmo confete.
    assert text == qz.quiz_ass(spec, tmp_path / 'q2.ass').read_text(encoding='utf-8')
    # O confete nunca passa do fim do bloco da pergunta.
    assert all(line.split(',')[2] <= '0:00:04.00' for line in confetti[:qz.CONFETTI_PIECES])

    pulse = qz.quiz_ass(qz.Quiz.model_validate(quiz_payload(reveal_effect="pulse")), tmp_path / 'p.ass')
    assert r'\move(' not in pulse.read_text(encoding='utf-8')
    plain = qz.quiz_ass(qz.Quiz.model_validate(quiz_payload(reveal_effect="none")), tmp_path / 'n.ass').read_text(encoding='utf-8')
    assert r'\t(' not in plain and r'\move(' not in plain
    with pytest.raises(ValidationError, match='som'):
        qz.Quiz.model_validate(quiz_payload(reveal_sound="custom"))


def test_real_ffmpeg_reveal_sound_lands_on_each_answer(tmp_path):
    from cutclips import sounds
    # Fundo mudo e sem música: todo som do vídeo vem da revelação.
    spec = qz.Quiz.model_validate(quiz_payload(suspense=2, reveal=1, reveal_sound="ding"))
    result = qz.render_quiz(spec, None, tmp_path, width=270, height=480)
    assert result['reveal'] == {'sound': 'ding', 'effect': 'confetti'}
    assert qz.reveal_times(spec) == [2, 5, 8]
    raw = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(tmp_path / 'clips/quiz.mp4'),
                          '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], capture_output=True).stdout
    samples = [abs(int.from_bytes(raw[i:i + 2], 'little', signed=True)) for i in range(0, len(raw) - 1, 2)]

    def loud(start, end):
        return max(samples[int(start * 8000):int(end * 8000)])

    for at in [2, 5, 8]:
        assert loud(at - .6, at - .05) < 200, at   # silêncio durante o suspense
        assert loud(at, at + .4) > 3000, at        # o som entra junto com a resposta
    assert sounds.preset_path('ding').is_file()


def test_reveal_sound_api_and_short_custom_sounds(client, tmp_path):
    listing = client.get('/api/quiz/sounds').json()['sounds']
    assert [s['id'] for s in listing] == ['acerto', 'ding', 'tada', 'pop']
    wav = client.get('/api/quiz/sounds/tada')
    assert wav.status_code == 200 and wav.content[:4] == b'RIFF'
    assert client.get('/api/quiz/sounds/buzina').status_code == 404

    ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=900:duration=0.3', str(tmp_path / 'blip.wav')], tmp_path)
    blip = (tmp_path / 'blip.wav').read_bytes()
    # Um efeito de 0,3s é válido como som de revelação, mas não como música.
    assert client.post('/api/music', files={'file': ('blip.wav', blip)}).status_code == 422
    sound = client.post('/api/music?kind=sound', files={'file': ('blip.wav', blip)}).json()
    assert sound['duration'] < 1
    ok = client.post('/api/quiz', json=quiz_payload(reveal_sound='custom', reveal_sound_file=sound['music']))
    assert ok.status_code == 202
    missing = client.post('/api/quiz', json=quiz_payload(reveal_sound='custom', reveal_sound_file='d' * 32 + '.wav'))
    assert missing.status_code == 422


def test_vs_puts_both_videos_in_a_band_with_badge_and_watermark(tmp_path):
    import cv2
    a, b = _sources(tmp_path)
    spec = rc.Reaction.model_validate(reaction_payload(layout='vs', badge='VS', watermark='meuperfil',
                                                       band_height=50, band_position=40, duration=1.2))
    assert spec.watermark == '@meuperfil'
    labels = rc.labels_ass(spec, 1.2, tmp_path / 'labels.ass').read_text(encoding='utf-8')
    # O selo pulsa: um evento por batida de 1,2s, e o @ fica no rodapé.
    assert labels.count('}VS\n') == 1 and 'ORIGINAL' in labels and '}@meuperfil\n' in labels
    width, band_height, top = rc.band_box(spec, 270, 480)
    assert (width, band_height, top) == (270, 240, 72)

    result = rc.render_reaction(spec, {'top': a, 'bottom': b}, tmp_path, width=270, height=480)
    assert result['layout'] == 'vs'
    info = probe(tmp_path / 'clips/reaction.mp4')
    assert info.has_audio and (info.width, info.height) == (270, 480)
    frame = tmp_path / 'vs.png'
    ffmpeg(['-ss', '0.4', '-i', str((tmp_path / 'clips/reaction.mp4').resolve()),
            '-frames:v', '1', str(frame.resolve())], tmp_path)
    image = cv2.imread(str(frame)).astype(int)
    # O divisor entre os dois vídeos é nítido dentro da faixa e some no fundo desfocado.
    edge = lambda row: float(np.abs(np.diff(image[row, 129:142].mean(axis=1))).max())
    assert min(edge(top + 40), edge(top + band_height - 40)) > 100
    assert max(edge(top - 15), edge(top + band_height + 15), edge(460)) < 20

    for bad in ({'watermark': 'nome com espaco'}, {'band_height': 10}, {'band_position': 95}):
        with pytest.raises(ValidationError):
            rc.Reaction.model_validate(reaction_payload(layout='vs', **bad))


def test_title_and_watermark_fonts_reach_the_burned_labels(tmp_path):
    spec = rc.Reaction.model_validate(reaction_payload(headline='Olha isso', headline_font='Impact', headline_size=76,
                                                       headline_position=8, watermark='meuperfil', watermark_font='Georgia'))
    labels = rc.labels_ass(spec, 2.0, tmp_path / 'labels.ass').read_text(encoding='utf-8')
    title = next(line for line in labels.splitlines() if line.startswith('Dialogue: 1'))
    mark = next(line for line in labels.splitlines() if '@meuperfil' in line)
    assert '\\fnImpact' in title and '\\fs76' in title and '\\pos(540,154)' in title
    assert '\\fnGeorgia' in mark  # o @ também vale no empilhado, não só no VS
    with pytest.raises(ValidationError):
        rc.Reaction.model_validate(reaction_payload(headline_font='Wingdings'))


def test_preview_source_downloads_a_window_once(client, tmp_path, monkeypatch):
    from api import preview as pv
    monkeypatch.setattr(pv, 'STORAGE', tmp_path)
    calls = []

    def fake(url, start, target):
        calls.append((url, start))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'mp4')
        return target

    monkeypatch.setattr(pv, 'download_window', fake)
    params = {'url': TT, 'start': 2.5}
    for _ in range(2):
        response = client.get('/api/preview/source', params=params)
        assert response.status_code == 200 and response.content == b'mp4'
    assert calls == [(TT, 2.5)]
    assert client.get('/api/preview/source', params={'url': TT, 'start': 9.0}).status_code == 200
    assert len(calls) == 2  # outro início, outro arquivo
    assert client.get('/api/preview/source', params={'url': 'https://exemplo.com/video'}).status_code == 422
    monkeypatch.setattr(pv, 'download_window', lambda *a: (_ for _ in ()).throw(RuntimeError('bloqueado')))
    assert client.get('/api/preview/source', params={'url': YT, 'start': 1}).status_code == 502
