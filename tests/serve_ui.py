"""Isolated UI fixture. Never reads/writes production jobs or invokes cloud AI."""
import os
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CUTCLIPS_STORAGE"] = str(ROOT / "tests/artifacts/ui-storage")
local_ffmpeg = ROOT / ".venv/Lib/site-packages/static_ffmpeg/bin/win32"
if local_ffmpeg.is_dir():
    os.environ["PATH"] = str(local_ffmpeg) + os.pathsep + os.environ["PATH"]

from api import db
from cutclips.config import Config
from cutclips.editor import render_clip, atomic_json
from cutclips.transcribe import Transcript, Word

if "--worker" in sys.argv:
    from api.worker import main
    main()
    raise SystemExit

db.init()
if not db.list_jobs():
    sample = ROOT / "tests/artifacts/pytest-run2/media0/sample.mp4"
    if not sample.is_file():
        raise SystemExit("Run the pytest suite with --basetemp=tests/artifacts/pytest-run2 first")
    job = db.create_job("sample.mp4", title="Demonstração de revisão", settings={"llm_provider":"heuristic", "preset":"ultrafast"})
    directory = Path(os.environ["CUTCLIPS_STORAGE"]) / "jobs" / job
    (directory / "source").mkdir(parents=True)
    shutil.copyfile(sample, directory / "source/sample.mp4")
    words = [Word(0.1,0.5,"Um"),Word(0.6,1,"teste",0.3),Word(1.1,1.5,"de"),Word(1.6,2,"edição."),
             Word(3.1,3.5,"Outra"),Word(3.6,4,"frase"),Word(4.1,4.5,"completa.")]
    Transcript("pt",12,words).to_json(directory / "transcript.json")
    clip = render_clip(directory / "source/sample.mp4", directory, words,
                       {"index":1,"title":"Um teste de edição", "hook":"Um teste de edição.","score":65,
                        "source_start":0,"source_end":5,"provider":"heuristic"},
                       Config(layout="manual",preset="ultrafast"))
    manifest={"source":"sample.mp4","source_duration":12,"source_resolution":"640x360",
              "provider":"heuristic","language":"pt","niche":"Educação","clips":[clip]}
    db.finish(job,manifest)
    atomic_json(directory / "manifest.json",manifest)

import uvicorn
uvicorn.run("api.main:app",host="127.0.0.1",port=8765)
