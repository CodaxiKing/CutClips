"""Isolated Top 5 UI fixture: run with python -P tests/serve_topfive_ui.py."""
import select
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT.parent))
sys.path.insert(0,str(ROOT))
os.environ['CLIPFORGE_STORAGE']=str(ROOT/'tests/artifacts/topfive-ui')
for env in ('.venv','.venv-imported'):
    ffmpeg=ROOT/env/'Lib/site-packages/static_ffmpeg/bin/win32'
    if ffmpeg.is_dir():
        os.environ['PATH']=str(ffmpeg)+os.pathsep+os.environ['PATH']
from api import db
from clipforge.topfive import TopFive,render_topfive,ffmpeg
from clipforge.editor import atomic_json
db.init()
if not db.list_jobs():
    spec=TopFive(headline='Teste local das quatro melhorias',entries=[
        {'url':f'https://www.tiktok.com/@test/video/{i+1}','name':f'Teste {i+1}','duration':1} for i in range(3)])
    job=db.create_job('top5',title=spec.headline,settings={'kind':'top5','top5':spec.model_dump()})
    folder=Path(os.environ['CLIPFORGE_STORAGE'])/'jobs'/job
    folder.mkdir(parents=True)
    source=folder/'source.mp4'
    ffmpeg(['-f','lavfi','-i','color=c=blue:size=180x320:rate=30:duration=1',
            '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-preset','ultrafast',
            '-c:a','aac','-shortest',str(source)],folder)
    manifest=render_topfive(spec,[source]*3,folder,width=180,height=320)
    atomic_json(folder/'manifest.json',manifest)
    db.finish(job,manifest)
import uvicorn
uvicorn.run('api.main:app',host='127.0.0.1',port=8766,access_log=False)
