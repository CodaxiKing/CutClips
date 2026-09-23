import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

from cutclips.boundaries import refine
from cutclips.captions import build_ass, build_srt, _ts
from cutclips.config import Config
from cutclips.editor import render_clip
from cutclips.probe import probe
from cutclips.reframe import (_camera_path, _fill_and_smooth, _plausible,
                               _saliency_centers, _targets_from_index)
from cutclips.segment import Sentence, build_sentences, transcript_outline
from cutclips.select import select_clips
from cutclips.transcribe import Transcript, Word, cache_signature
from cutclips.transcribe import _correct_names
from cutclips.signals import opening_signals
from cutclips.editing import edit_ranges, remap_words
import cutclips.select as selector
import cutclips.transcribe as transcription
import cutclips.download as downloader
import cutclips.prospect as prospect
import cutclips.render as renderer
from cutclips.run import process


def test_caption_fast_words_do_not_overlap(tmp_path):
    words=[Word(0,0.1,"Uma"),Word(0.02,0.2,"fala"),Word(0.03,0.3,"rápida."),Word(0.35,0.5,"Fim.")]
    path=build_ass(words,0,0.6,tmp_path/"a.ass")
    def seconds(s):
        h,m,sec=s.split(":");return int(h)*3600+int(m)*60+float(sec)
    events=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue"):
            fields=line.split(",")
            events.append((seconds(fields[1]),seconds(fields[2])))
    assert events and all(a<b<=0.6 for a,b in events)
    assert all(b<=c for (_,b),(c,_) in zip(events,events[1:]))
    assert _ts(59.999)=="0:01:00.00"
    assert _ts(3599.999)=="1:00:00.00"


def test_srt_relative_and_corrected(tmp_path):
    path=build_srt([Word(10,10.5,"Olá"),Word(10.6,11,"mundo!")],10,11,tmp_path/"x.srt")
    text=path.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,000" in text and "Olá mundo!" in text


def test_contiguous_phrases_keep_own_syllables():
    assert refine(2,4,2,4,10,[],Config())==(2,4)


def test_selection_sorts_before_overlap_and_reassesses(transcript,monkeypatch):
    calls=[]
    def provider(system,user,cfg):
        calls.append(user)
        if len(calls)==1:
            return json.dumps({"clips":[{"first":0,"last":1,"score":5},{"first":0,"last":0,"score":90,"hook":"inventado"},{"first":3,"last":2,"score":99},{"first":0.5,"last":0,"score":100}]})
        return json.dumps({"reviews":[{"id":0,"score":85,"title":"Um título fiel","criteria":{"gancho":4},"description":"Resumo"}]})
    monkeypatch.setitem(selector.PROVIDERS,"anthropic",provider)
    plans=select_clips(build_sentences(transcript),cfg=Config(min_duration=0.5,max_duration=8,max_clips=1,niche="educação",audience="iniciantes"))
    assert len(plans)==1 and plans[0].last==0
    assert plans[0].hook=="O erro tem solução."
    assert plans[0].title=="Um título fiel" and plans[0].score==85
    assert "educação" in calls[0] and "iniciantes" in calls[0]
    assert json.loads(calls[1])["clips"][0]["text"]==plans[0].text


def test_fallback_provenance_and_bad_score(transcript,monkeypatch):
    def fail(*args): raise RuntimeError("secret text must not leak")
    monkeypatch.setitem(selector.PROVIDERS,"anthropic",fail)
    plans=select_clips(build_sentences(transcript),cfg=Config(min_duration=0.5,max_duration=5))
    assert plans and all(p.provider=="heuristic" for p in plans)
    assert "secret" not in str([p.warnings for p in plans])
    def bad(*args):return json.dumps({"clips":[{"first":0,"last":0,"score":"NaN"}]})
    monkeypatch.setitem(selector.PROVIDERS,"anthropic",bad)
    assert select_clips(build_sentences(transcript),cfg=Config(min_duration=0.5,max_duration=5))[0].score==0


def test_transcription_cache_identity_and_zero_confidence(tmp_path,monkeypatch):
    video=tmp_path/"source.mp4";video.write_bytes(b"source")
    calls=[]
    def fake_model(*args):
        calls.append(args)
        return SimpleNamespace(transcribe=lambda *a,**k:([SimpleNamespace(words=[SimpleNamespace(word="texto",start=0,end=1,probability=0)])],SimpleNamespace(language="pt",duration=2)))
    monkeypatch.setattr(transcription,"_model",fake_model)
    cfg=Config(whisper_device="cpu", whisper_model="large-v3")
    cache=tmp_path/"transcript.json"
    assert transcription.transcribe(video,cache,cfg).words[0].prob==0
    transcription.transcribe(video,cache,cfg)
    assert len(calls)==1
    transcription.transcribe(video,cache,replace(cfg,whisper_model="small"))
    assert len(calls)==2
    video.write_bytes(b"changed source")
    transcription.transcribe(video,cache,replace(cfg,whisper_model="small"))
    assert len(calls)==3


@pytest.mark.parametrize("layout", ["track","active","manual","fit","split"])
def test_real_render_all_layouts(layout,sample,transcript,tmp_path):
    cfg=Config(layout=layout,out_width=360,out_height=640,preset="ultrafast",denoise_audio=True)
    clip=render_clip(sample,tmp_path,transcript.words,{"index":1,"title":"Teste","source_start":0.1,"source_end":2.1},cfg)
    info=probe(tmp_path/"clips"/clip["file"])
    assert (info.width,info.height)==(360,640) and info.has_audio
    assert abs(info.duration-2)<1/30
    assert len(clip["thumbnails"])==3
    assert (tmp_path/"clips"/clip["subtitle"]).is_file()


def test_pipeline_offline_fallback_and_exact_duration(sample,transcript,tmp_path,monkeypatch):
    cfg=Config(llm_provider="anthropic",min_duration=1,max_duration=3,max_clips=1,
               out_width=1080,out_height=1920,preset="ultrafast",layout="manual")
    transcript.to_json(tmp_path/"transcript.json")
    data=json.loads((tmp_path/"transcript.json").read_text(encoding="utf-8"));data["signature"]=cache_signature(sample,cfg)
    (tmp_path/"transcript.json").write_text(json.dumps(data),encoding="utf-8")
    def unavailable(*args):raise RuntimeError("offline test")
    monkeypatch.setitem(selector.PROVIDERS,"anthropic",unavailable)
    result=process(sample,tmp_path,cfg)
    assert result["provider"]=="heuristic" and result["requested_provider"]=="anthropic"
    clip=result["clips"][0]
    assert clip["planned_duration"]<=3
    assert abs(clip["planned_duration"]-clip["actual_duration"])<1/30
    assert probe(tmp_path/"clips"/clip["file"]).width==1080


def test_sentinel_proxy_is_disabled_for_ytdlp(tmp_path, monkeypatch):
    captured = {}
    class FakeYDL:
        def __init__(self, opts): captured.update(opts)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, *args, **kwargs): return {"id":"x", "_type":"video"}
        def process_ie_result(self, info, download=True):
            path = tmp_path / "x.mp4"; path.write_bytes(b"video")
            return {**info, "requested_downloads":[{"filepath":str(path)}]}
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    path, _ = downloader.download("https://youtu.be/example", tmp_path)
    assert path.is_file() and captured["proxy"] == ""


def test_camera_locks_a_nearly_stationary_subject():
    cfg = Config(center_bias=0.4, stationary_threshold=0.2, deadzone=0.1)
    path, mode = _camera_path(np.array([580, 584, 578, 582]), 600, 1200, 2, 30, cfg)
    assert mode == "locked"
    assert path == [(0.0, 600)]


def test_camera_pan_is_dense_and_speed_limited():
    cfg = Config(center_bias=0, stationary_threshold=0.01, deadzone=0,
                 smooth_seconds=0.2, max_pan_speed=0.3, motion_fps=30, track_fps=4)
    path, mode = _camera_path(np.array([100, 100, 1100, 1100]), 600, 1200, 1, 30, cfg)
    assert mode == "smooth" and len(path) > 5
    max_step = cfg.max_pan_speed * 600 / cfg.motion_fps
    assert max(abs(b[1] - a[1]) for a, b in zip(path, path[1:])) <= max_step + 1
    max_speed = cfg.max_pan_speed * 600
    assert max(abs(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(path, path[1:])) <= max_speed + 2


def test_decoding_settings_follow_the_device_and_stay_overridable():
    from cutclips.transcribe import _beam_size, _decode_threads
    cpu = Config()
    # Sem GPU a busca em feixe domina o tempo; com GPU ela é barata.
    assert _beam_size("cpu", cpu) == 2 and _beam_size("cuda", cpu) == 5
    assert _decode_threads("cpu", cpu) >= 1
    assert _decode_threads("cuda", cpu) == 0      # a biblioteca decide na GPU
    manual = Config(whisper_beam=5, whisper_threads=3)
    assert _beam_size("cpu", manual) == 5 and _decode_threads("cpu", manual) == 3
    manual.validate()


def test_transcript_cache_invalidates_when_the_beam_changes(tmp_path):
    from cutclips.transcribe import cache_signature
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 64)
    a = cache_signature(video, Config(whisper_beam=0))
    b = cache_signature(video, Config(whisper_beam=5))
    assert a != b, "mudar o feixe precisa refazer a transcrição em cache"


def test_missing_api_key_says_what_to_do_instead_of_a_class_name(transcript, monkeypatch):
    """Foi uma chave vazia que manteve a seleção por IA desligada sem ninguém ver."""
    from cutclips.diagnostics import explain
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    plans = select_clips(build_sentences(transcript),
                         cfg=Config(llm_provider="anthropic", min_duration=0.5, max_duration=5))
    assert plans and all(p.provider == "heuristic" for p in plans)
    reason = plans[0].fallback_reason
    assert "ANTHROPIC_API_KEY" in reason and ".env" in reason
    assert "TypeError" not in reason
    assert reason in plans[0].warnings[0]


def test_explain_curates_known_failures_and_never_echoes_the_raw_message():
    from cutclips.diagnostics import explain
    import json
    assert "crédito" in explain(Exception("rate_limit_error: quota exceeded"))
    assert "recusada" in explain(Exception("AuthenticationError: invalid api key"))
    assert "conexão" in explain(Exception("APIConnectionError: network is unreachable"))
    assert "formato" in explain(json.JSONDecodeError("Expecting value", "", 0))
    # Desconhecido: nome da classe e ponteiro para o log, nunca o texto original.
    unknown = explain(RuntimeError("prompt secreto e chave sk-ant-123 aqui"))
    assert "sk-ant-123" not in unknown and "secreto" not in unknown
    assert "RuntimeError" in unknown and "log do worker" in unknown


def _observations(pairs, samples=40):
    """Amostras sintéticas de rostos: (cx, cy, altura) em px da amostra."""
    return [[(x, 135.0, h) for x, h in pairs] for _ in range(samples)]


def test_two_people_too_far_apart_become_a_split_screen():
    from cutclips.reframe import _split_decision, split_tile_width
    from cutclips.probe import MediaInfo
    cfg = Config()
    info = MediaInfo(Path("x.mp4"), 60.0, 1920, 1080, 30.0, True)
    # Amostra tem 480 px de largura para 1920 na origem: escala 4.
    apart = _observations([(60.0, 40.0), (420.0, 40.0)])
    split = _split_decision(apart, 4.0, 608, info, cfg)
    assert split is not None
    left, right = split
    assert 0 <= left < right <= 1
    # As duas posições precisam render quadros distintos de verdade.
    room = 1920 - split_tile_width(info, cfg)
    assert (right - left) * room > 200


def test_two_people_that_share_a_frame_keep_one_camera():
    from cutclips.reframe import _split_decision
    from cutclips.probe import MediaInfo
    info = MediaInfo(Path("x.mp4"), 60.0, 1920, 1080, 30.0, True)
    close = _observations([(230.0, 40.0), (260.0, 40.0)])
    assert _split_decision(close, 4.0, 608, info, Config()) is None


def test_a_passer_by_does_not_trigger_the_split():
    from cutclips.reframe import _split_decision
    from cutclips.probe import MediaInfo
    info = MediaInfo(Path("x.mp4"), 60.0, 1920, 1080, 30.0, True)
    samples = _observations([(240.0, 40.0)], samples=36) + _observations([(60.0, 40.0), (420.0, 40.0)], samples=4)
    assert _split_decision(samples, 4.0, 608, info, Config()) is None


def test_a_wide_shot_zooms_in_without_destroying_sharpness():
    from cutclips.reframe import _zoom_factor
    cfg = Config()
    # Rosto de 12 px numa amostra escala 4 = 48 px numa altura de recorte de 1080.
    small = _zoom_factor(_observations([(240.0, 12.0)]), 4.0, 1080, cfg)
    assert small < 1.0 and small >= cfg.min_zoom
    # A ampliação total do recorte até a saída respeita o teto configurado.
    assert cfg.out_height / (1080 * small) <= cfg.max_upscale + 0.01
    close_up = _zoom_factor(_observations([(240.0, 90.0)]), 4.0, 1080, cfg)
    assert close_up == 1.0
    assert _zoom_factor([], 4.0, 1080, cfg) == 1.0
    assert _zoom_factor(_observations([(240.0, 12.0)]), 4.0, 1080, Config(auto_zoom=False)) == 1.0


def test_camera_keeps_an_off_center_subject_inside_the_safe_area():
    # 1920x1080 -> recorte 9:16 de 608px. Apresentador a 15% da largura: o antigo
    # viés de centro deixava o rosto a 10% do recorte, ou seja, meio rosto cortado.
    cfg = Config()
    crop_w, max_x = 608, 1920 - 608
    for fraction in (0.05, 0.15, 0.3, 0.7, 0.9):
        face = fraction * 1920
        path, _ = _camera_path(np.full(40, face - crop_w / 2), crop_w, max_x, 10, 30, cfg)
        inside = (face - path[0][1]) / crop_w
        assert 0.12 <= inside <= 0.88, (fraction, inside)


def test_camera_starts_already_framed_on_the_subject():
    cfg = Config()
    crop_w, max_x = 608, 1312
    face = 0.2 * 1920
    path, _ = _camera_path(np.full(40, face - crop_w / 2), crop_w, max_x, 10, 30, cfg)
    assert abs((face - path[0][1]) / crop_w - 0.5) < 0.3   # nada de rampa a partir do centro


def test_camera_crosses_the_frame_in_one_linear_move():
    cfg = Config()
    targets = np.linspace(100, 1200, 40)
    path, mode = _camera_path(targets, 608, 1312, 10, 30, cfg)
    assert mode == "linear"
    steps = [b[1] - a[1] for a, b in zip(path, path[1:])]
    assert all(s > 0 for s in steps)                      # nenhum recuo
    assert max(steps) - min(steps) <= 1                   # velocidade constante


def test_camera_stops_chasing_a_subject_that_keeps_coming_back():
    # Streamer balançando na cadeira: perseguir vira vaivém, e vaivém cansa mais
    # que um enquadramento ligeiramente descentralizado.
    cfg = Config()
    targets = 500 + 150 * np.sin(np.linspace(0, 8 * np.pi, 40))
    path, mode = _camera_path(targets, 608, 1312, 10, 30, cfg)
    xs = [x for _, x in path]
    assert mode == "linear"
    assert max(xs) - min(xs) < 0.12 * 608


def test_camera_still_tracks_positions_too_far_apart_to_share_a_frame():
    cfg = Config()
    targets = 400 + 300 * np.sin(np.linspace(0, 8 * np.pi, 40))
    path, mode = _camera_path(targets, 608, 1312, 10, 30, cfg)
    assert mode == "smooth"


def test_tracking_holds_the_last_position_through_a_long_gap():
    cfg = Config(track_fps=4, smooth_seconds=0.25, hold_seconds=1.0)
    centers = [100.0] * 4 + [None] * 12 + [400.0] * 4
    filled = _fill_and_smooth(centers, 250.0, cfg)
    assert filled[8] < 150   # segurou onde estava em vez de atravessar o quadro
    assert filled[-1] > 350


def test_saliency_follows_the_busy_side_when_nobody_appears():
    frames = np.zeros((6, 90, 160, 3), dtype=np.uint8)
    rng = np.random.default_rng(7)
    for i in range(6):
        frames[i, 20:70, 110:150] = rng.integers(0, 255, (50, 40, 3), dtype=np.uint8)
    centers = _saliency_centers(frames, 60)
    assert centers is not None and centers.mean() > 100


def test_background_faces_do_not_steal_the_camera():
    speaker = (400, 200, 200, 220)
    poster = (40, 30, 40, 44)
    assert _plausible([speaker, poster]) == [speaker]


def test_index_targets_are_rejected_when_faces_are_rare():
    info = SimpleNamespace(width=1920, height=1080, fps=30)
    index = {"faces": [{"time": float(t), "faces": []} for t in range(20)]}
    index["faces"][3]["faces"] = [{"x": 0.5, "y": 0.5, "size": 0.02}]
    assert _targets_from_index(index, info, 0, 20, 608, 1312, Config()) is None


def test_supported_hosts_cover_the_three_platforms_and_nothing_else():
    assert downloader.platform_of("https://www.twitch.tv/videos/123") == "twitch"
    assert downloader.platform_of("https://clips.twitch.tv/SomeClip") == "twitch"
    assert downloader.platform_of("https://kick.com/canal") == "kick"
    assert downloader.platform_of("https://youtu.be/abcdefghijk") == "youtube"
    for rejected in ("https://vimeo.com/1", "http://localhost/x", "file:///etc/passwd",
                     "https://twitch.tv.evil.com/v", "not a url"):
        assert downloader.platform_of(rejected) is None
    assert downloader.is_youtube_url("https://youtube.com/watch?v=x")
    assert not downloader.is_youtube_url("https://kick.com/x")


def test_live_link_is_refused_unless_recording_was_requested(monkeypatch, tmp_path):
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True, process=True):
            return {"id": "live1", "is_live": True, "formats": []}
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(downloader.DownloadError, match="ao vivo"):
        downloader.download("https://www.twitch.tv/canal", tmp_path)


def test_prospect_finds_the_loud_moments_and_transcribes_only_those():
    hz = prospect.ENVELOPE_HZ
    curve = np.full(int(600 * hz), 0.001)
    for peak in (120.0, 300.0, 500.0):
        start = int(peak * hz)
        curve[start:start + int(5 * hz)] = 0.5
    moments = prospect.find_moments(Path("x.mp4"), 600.0, Config(), None, 10, curve)
    centers = sorted((m.start + m.end) / 2 for m in moments)
    assert len(centers) == 3
    for found, expected in zip(centers, (120.0, 300.0, 500.0)):
        assert abs(found - (expected + 2.5 - 45 * 0.2)) < 20
    windows = prospect.analysis_windows(moments, 600.0, Config())
    analysed = sum(b - a for a, b in windows)
    assert analysed < 600.0 * 0.6          # muito menos que a transmissão inteira


def test_prospect_falls_back_to_sampling_a_silent_stream():
    assert prospect.find_moments(Path("x.mp4"), 600.0, Config(), None, 10, np.zeros(4800)) == []
    windows = prospect.fallback_windows(3600.0, Config(), 8)
    assert len(windows) == 8 and all(0 <= a < b <= 3600 for a, b in windows)


def test_horizontal_orientation_keeps_the_whole_frame():
    from cutclips.config import apply_orientation
    from cutclips.reframe import crop_geometry
    cfg = apply_orientation(Config(), "horizontal")
    cfg.validate()
    info = SimpleNamespace(width=1920, height=1080)
    assert (cfg.out_width, cfg.out_height) == (1920, 1080)
    assert crop_geometry(info, cfg) == (1920, 1080)
    with pytest.raises(ValueError):
        apply_orientation(Config(), "diagonal")


def test_emphasis_marks_the_loudest_moments_and_skips_cut_ranges():
    from cutclips.editor import energy_accents
    index = {"audio_words": [{"start": float(i), "end": i + .4, "energy": 0.3} for i in range(40)]}
    for i, value in ((5, 0.99), (6, 0.98), (20, 0.97), (33, 0.96)):
        index["audio_words"][i]["energy"] = value
    cuts = [(19.0, 21.0, "silêncio")]
    accents = energy_accents(index, 0.0, 40.0, cuts, limit=3)
    assert 5.0 in accents                      # pico mais alto
    assert all(abs(a - 20.0) > 0.5 for a in accents)   # dentro de corte, descartado
    assert all(b - a >= 2.5 for a, b in zip(accents, accents[1:]))   # sem pisca-pisca
    assert energy_accents({"audio_words": []}, 0, 40, []) == []


def test_live_pipeline_prospects_before_transcribing(tmp_path, monkeypatch):
    """Transmissão longa: garimpa, transcreve só os picos e entrega 16:9."""
    import subprocess
    import cutclips.run as runner
    from cutclips.config import apply_orientation
    from cutclips.transcribe import Transcript, Word

    video = tmp_path / "live.mp4"
    bursts = "+".join(f"between(t,{a},{a + 6})" for a in (60, 130))
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=5:duration=180",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=180",
                    "-filter_complex", f"[1:a]volume='0.04+0.9*({bursts})':eval=frame[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "34", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(video)], check=True, capture_output=True)

    seen = {}

    def fake_transcribe(path, cache=None, cfg=None, context="", windows=None):
        seen["windows"] = windows
        phrases = ["Olha o que aconteceu agora nesse round inteiro.",
                   "Eu nunca tinha visto uma virada assim antes.",
                   "E o time inteiro caiu junto no final."]
        words = []
        for base in (45.0, 115.0):
            clock = base
            for phrase in phrases:
                for token in phrase.split():
                    words.append(Word(clock, clock + 0.45, token, 0.99))
                    clock += 0.55
                clock += 0.9
        return Transcript("pt", 180.0, words)

    monkeypatch.setattr(runner, "transcribe", fake_transcribe)
    cfg = apply_orientation(Config(llm_provider="heuristic", prospect_after_minutes=1.0,
                                   max_clips=2, min_duration=8.0, max_duration=40.0,
                                   target_duration=20.0, preset="ultrafast",
                                   diarization=False), "horizontal")
    manifest = runner.process(video, tmp_path / "job", cfg=cfg, write_manifest=False)

    assert seen["windows"], "transmissão longa deveria transcrever só as janelas garimpadas"
    analysed = sum(b - a for a, b in seen["windows"])
    assert analysed < 180 * 0.8
    assert manifest["prospect"]["used"] and manifest["prospect"]["moments"]
    assert manifest["orientation"] == "horizontal"
    assert manifest["clips"], "nenhum corte saiu do garimpo"
    for clip in manifest["clips"]:
        info = probe(tmp_path / "job" / "clips" / clip["file"])
        assert (info.width, info.height) == (1920, 1080)


def test_sentence_context_preserves_speaker_pause_and_question():
    tr = Transcript("pt", 4, [Word(0, .4, "Por", speaker="SPEAKER_00"),
        Word(.4, .8, "quê?", speaker="SPEAKER_00"), Word(2.5, 3, "Resposta.", speaker="SPEAKER_01")])
    sentences = build_sentences(tr)
    outline = transcript_outline(sentences)
    assert sentences[1].topic_boundary and sentences[1].pause_before > 1
    assert "SPEAKER_00" in outline and "pausa" in outline and "mudança" in outline


def test_hierarchical_analysis_and_review_are_cached(tmp_path, monkeypatch):
    sentences = [Sentence(i, i*20, i*20+10, f"Ideia {i} tem resultado.",
                  [Word(i*20, i*20+10, f"Ideia{i}.")]) for i in range(20)]
    calls = []
    def provider(system, user, cfg):
        calls.append(system)
        if "Resuma todos" in system:
            return json.dumps({"blocks":[{"id":0,"summary":"tema","topic":"tema","score":90}]})
        if "segunda revisão" in system:
            return json.dumps({"reviews":[{"id":0,"approved":True,"first":1,"last":2,"score":88,
                "criteria":{"gancho":4,"clareza":5,"conclusao":5}}]})
        return json.dumps({"clips":[{"first":1,"last":2,"score":80,"topic":"tema"}]})
    monkeypatch.setitem(selector.PROVIDERS, "anthropic", provider)
    cfg = Config(min_duration=10, max_duration=60, max_clips=1, analysis_block_seconds=120)
    cache = tmp_path / "analysis.json"
    assert select_clips(sentences, cfg=cfg, cache=cache)[0].score == 88
    assert len(calls) == 3
    assert select_clips(sentences, cfg=cfg, cache=cache)[0].score == 88
    assert len(calls) == 3


def test_low_confidence_proper_name_correction_is_conservative():
    words = [Word(0, 1, "Sacanni", .55), Word(1, 2, "assunto", .99)]
    corrections = _correct_names(words, "Entrevista com Sérgio Sacani sobre astronomia")
    assert words[0].text == "Sacani" and corrections[0]["from"] == "Sacanni"
    assert words[1].text == "assunto"


def test_opening_audiovisual_signals_are_bounded(sample):
    data = opening_signals(sample, 0, 2)
    assert 0 <= data["audiovisual_hook"] <= 5
    assert 0 <= data["audio_energy"] <= 1 and 0 <= data["visual_change"] <= 1


def test_internal_edit_removes_long_pause_and_remaps_timeline():
    words=[Word(0,.5,"Olá"),Word(2.2,2.6,"mundo")]
    cuts=edit_ranges(words,0,3,Config(internal_silence_seconds=1))
    mapped=remap_words(words,0,cuts)
    assert cuts and cuts[0][2]=="silêncio"
    assert mapped[1].start < words[1].start


def test_gpu_encoder_selection_and_cpu_fallback(monkeypatch):
    renderer.available_video_encoders.cache_clear()
    monkeypatch.setattr(renderer,"available_video_encoders",lambda:{"h264_qsv","libx264"})
    assert renderer.select_video_encoder("auto")=="h264_qsv"
    assert renderer.select_video_encoder("nvenc")=="libx264"


def test_incremental_caption_edit_reuses_base(sample, transcript, tmp_path):
    cfg=Config(layout="manual",out_width=360,out_height=640,preset="ultrafast",video_encoder="cpu")
    clip={"index":1,"title":"Cache","source_start":.1,"source_end":2.1}
    first=render_clip(sample,tmp_path,transcript.words,clip,cfg)
    second=render_clip(sample,tmp_path,transcript.words,first,cfg,
                       edit={"start":.1,"end":2.1,"settings":{},"words":[{"id":0,"text":"Novo"}]})
    assert not first["incremental_cache_hit"] and second["incremental_cache_hit"]
    assert (tmp_path/"clips"/second["file"]).is_file()


def test_real_internal_edit_compresses_gap(sample,tmp_path):
    words=[Word(.1,.5,"Começo."),Word(2.5,3,"Fim.")]
    cfg=Config(layout="manual",out_width=360,out_height=640,preset="ultrafast",video_encoder="cpu",
               internal_silence_seconds=1)
    clip=render_clip(sample,tmp_path,words,{"index":1,"title":"Corte","source_start":0,"source_end":3.5},cfg)
    assert clip["internal_edits"] and clip["actual_duration"] < 3


def test_partial_overlap_is_trimmed_instead_of_dropped(transcript, monkeypatch):
    def provider(system, user, cfg):
        if '"reviews"' in system or 'reviews' in user[:200]:
            return json.dumps({"reviews": []})
        return json.dumps({"clips": [{"first": 0, "last": 1, "score": 90},
                                     {"first": 1, "last": 3, "score": 80},
                                     {"first": 0, "last": 1, "score": 70}]})
    monkeypatch.setitem(selector.PROVIDERS, "anthropic", provider)
    plans = select_clips(build_sentences(transcript), cfg=Config(min_duration=.5, max_duration=8, max_clips=5))
    spans = [(p.first, p.last) for p in plans]
    # O segundo dividia a frase 1 com o primeiro: entra encurtado, em vez de sumir.
    assert spans[0] == (0, 1) and spans[1][0] == 2
    # O terceiro repetia um trecho inteiro já usado e continua fora.
    assert len(spans) == 2 and len({s[0] for s in spans}) == 2


def test_free_span_finds_the_largest_unused_stretch():
    assert selector.free_span(0, 10, []) == (0, 10)
    assert selector.free_span(0, 10, [(3, 5)]) == (6, 10)      # o maior lado livre
    assert selector.free_span(0, 10, [(0, 2)]) == (3, 10)
    assert selector.free_span(0, 10, [(0, 10)]) is None
    assert selector.free_span(2, 4, [(0, 3)]) == (4, 4)


def test_triage_classifies_the_video_and_stitches_neighbour_blocks(monkeypatch):
    # Vinte blocos de 130s: só alguns são analisados, e a triagem diz o tipo do vídeo.
    sentences = [Sentence(id=i, start=i * 130.0, end=i * 130.0 + 120, text=f"Frase {i}.", words=[]) for i in range(20)]
    calls = []

    def provider(system, user, cfg):
        calls.append((system, user))
        if "Classifique o vídeo" in system:
            return json.dumps({"video_type": "tutorial",
                               "blocks": [{"id": i, "summary": "x", "score": 90 if i in (2, 4, 9) else 1}
                                          for i in range(20)]})
        return json.dumps({"clips": [{"first": 2, "last": 2, "score": 88}]})

    monkeypatch.setitem(selector.PROVIDERS, "anthropic", provider)
    cfg = Config(min_duration=.5, max_duration=200, max_clips=3, shortlist_multiplier=1,
                 analysis_block_seconds=120, analysis_context_sentences=1)
    clips = selector._hierarchical_candidates(sentences, "Aula", "pt", cfg, {}, None)
    assert clips and clips[0]["first"] == 2
    selection = calls[-1][1]
    assert "Tipo de vídeo: tutorial" in selection and "terminar no resultado" in selection
    outline_ids = {int(i) for i in re.findall(r"\[(\d+)\]", selection)}
    # Emendas curtas entre os blocos escolhidos (2, 4 e 9) entram inteiras.
    assert {1, 2, 3, 4, 5, 6, 7, 8, 9, 10} <= outline_ids
    # O resto do vídeo continua de fora: a costura fecha buracos, não abre o vídeo todo.
    assert not {0, 12, 15, 19} & outline_ids
