import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import db, main, studio
from clipforge.transcribe import Transcript, Word

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FFMPEG = ROOT / ".venv/Lib/site-packages/static_ffmpeg/bin/win32"
if LOCAL_FFMPEG.is_dir():
    os.environ["PATH"] = str(LOCAL_FFMPEG) + os.pathsep + os.environ["PATH"]


@pytest.fixture(scope="session")
def sample(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.fail("Instale FFmpeg e FFprobe para os testes de renderização")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=12", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=12", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def transcript():
    phrases = ["O erro tem solução.", "Veja como corrigir isso.", "Agora o resultado funciona.", "Confira o próximo exemplo."]
    words = []
    for i, phrase in enumerate(phrases):
        for n, token in enumerate(phrase.split()):
            words.append(Word(i*3+n*0.5+0.1, i*3+n*0.5+0.5, token, 0.4 if n == 1 else 0.99))
    return Transcript("pt", 12, words)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "clipforge.db")
    monkeypatch.setattr(main, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(studio, "JOBS", tmp_path / "jobs")
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def completed(client, sample, transcript):
    job_id = db.create_job("sample.mp4", title="Projeto de teste", settings={"llm_provider":"heuristic", "niche":"educação"})
    directory = studio.job_dir(job_id)
    (directory / "source").mkdir(parents=True)
    (directory / "clips").mkdir()
    shutil.copyfile(sample, directory / "source/sample.mp4")
    shutil.copyfile(sample, directory / "clips/original.mp4")
    transcript.to_json(directory / "transcript.json")
    manifest = {"source":"sample.mp4", "source_duration":12, "source_resolution":"640x360",
                "provider":"heuristic", "niche":"educação", "clips":[{"index":1,"revision":0,
                "source_start":0,"source_end":3,"actual_duration":3,"planned_duration":3,
                "title":"Um clipe", "text":"O erro tem solução.","hook":"O erro tem solução.",
                "score":50,"file":"original.mp4", "thumbnail":None}]}
    db.finish(job_id, manifest)
    return job_id, directory
