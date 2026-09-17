import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import db, main, studio, worker
from clipforge import topfive as t5
from clipforge.probe import probe


def payload():
    return {"headline":"Top 5 momentos incríveis", "entries":[
        {"url":f"https://www.tiktok.com/@test/video/{7000000000000000000+i}","name":f"Momento {i+1}"} for i in range(5)]}


@pytest.fixture
def workdir(tmp_path):
    # tmp_path guarda as últimas execuções e descarta as antigas sozinho: o vídeo
    # montado continua disponível para conferir (o teste imprime o caminho) sem
    # encher o repositório a cada `pytest`.
    return tmp_path


@pytest.fixture
def client(workdir,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',workdir/'test.db')
    monkeypatch.setattr(main,'JOBS',workdir/'jobs')
    monkeypatch.setattr(studio,'JOBS',workdir/'jobs')
    monkeypatch.setattr(worker,'STORAGE',workdir)
    with TestClient(main.app) as connection:
        yield connection


def test_validation():
    for url in ['http://www.tiktok.com/@x/video/123','https://tiktok.com.evil.org/@x/video/123','https://www.tiktok.com/@x','https://www.tiktok.com/@x/live','https://user@www.tiktok.com/@x/video/123','https://127.0.0.1/video/123']:
        value=payload();value['entries'][0]['url']=url
        with pytest.raises(ValidationError):t5.TopFive.model_validate(value)
    value=payload();value['entries'][0]['url']='https://vm.tiktok.com/Abc123/'
    assert t5.TopFive.model_validate(value)
    for update in [{'entries':payload()['entries'][:2]},{'headline':'   '},{'order':'random'}]:
        with pytest.raises(ValidationError):t5.TopFive.model_validate({**payload(),**update})


def test_queue_has_only_topfive_stage_and_recovery(client):
    response=client.post('/api/top5',json=payload())
    assert response.status_code==202
    job_id=response.json()['job_id']
    db.init()
    with db.connect() as c:
        assert [r['stage'] for r in c.execute('SELECT stage FROM pipeline_stages WHERE job_id=?',(job_id,))]==['top5']
    task=db.claim_pipeline_stage()
    assert task['stage']=='top5' and db.claim_pipeline_stage() is None
    assert not db.finish_topfive({**task,'token':'stale'}, {'clips':[]})
    db.fail_pipeline_stage(task,'teste')
    assert db.retry(job_id)
    task=db.claim_pipeline_stage()
    assert db.finish_topfive(task,{'clips':[],'kind':'top5'})
    assert db.get_job(job_id)['status']=='done'


def test_worker_routes_without_transcription(client,monkeypatch):
    job_id=client.post('/api/top5',json=payload()).json()['job_id']
    monkeypatch.setattr(t5,'process_topfive',lambda settings,directory,progress:{'kind':'top5','clips':[]})
    monkeypatch.setattr(worker,'transcribe',lambda *a,**k:pytest.fail('Top5 must not transcribe'))
    worker.run_pipeline_stage(db.claim_pipeline_stage())
    assert db.get_job(job_id)['status']=='done'


def test_ranking_reveals_in_playback_order_and_escapes_ass(workdir):
    value=payload();value['order']='countdown';value['entries'][4]['name']=r'Nome {\pos(0,0)}'
    spec=t5.TopFive.model_validate(value)
    timeline=[{'rank':5-i,'start':i,'end':i+1} for i in range(5)]
    path=workdir/'overlay.ass';t5.ranking_ass(spec,timeline,path)
    text=path.read_text(encoding='utf-8')
    first=[s for s in text.splitlines() if s.startswith('Dialogue: 2,0:00:00.00,0:00:01.00')]
    assert len(first)==6  # all five numbers, only one revealed name
    assert 'Momento 1' not in '\n'.join(first)
    assert r'Nome {\pos' not in text
    assert 'Momento 1' in text


@pytest.mark.parametrize('count', [3, 5])
def test_real_ffmpeg_mixed_sources(workdir, count, monkeypatch):
    a,b=workdir/'landscape.mp4',workdir/'portrait.mp4'
    t5.ffmpeg(['-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=1.4',
               '-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=1.4',
               '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(a.resolve())],workdir)
    t5.ffmpeg(['-f','lavfi','-i','color=c=blue:size=180x320:rate=30:duration=1.4',
               '-c:v','libx264','-preset','ultrafast',str(b.resolve())],workdir)
    value=payload();value['order']='countdown';value['entries']=value['entries'][:count]
    for entry in value['entries']:entry.update(start=.2,duration=1)
    spec=t5.TopFive.model_validate(value)
    result=t5.render_topfive(spec,[a,b,a,b,a][:count],workdir,width=270,height=480)
    info=probe(workdir/'clips/top5.mp4')
    assert info.has_audio and info.width==270 and info.height==480
    assert info.fps==30 and abs(info.duration-count)<.1
    # Uma nova montagem com os mesmos trechos não recodifica nenhuma parte.
    encoded=[]
    real_ffmpeg=t5.ffmpeg
    monkeypatch.setattr(t5,'ffmpeg',lambda args,cwd:(encoded.append(args[-1]),real_ffmpeg(args,cwd)))
    t5.render_topfive(spec,[a,b,a,b,a][:count],workdir,width=270,height=480)
    assert encoded and not any(str(name).startswith('part-') for name in encoded)
    assert [x['rank'] for x in result['timeline']]==list(range(count,0,-1))
    assert (workdir/'clips/top5-cover.jpg').is_file()
    # The silent sources still have normalized audio tracks for gap-free concatenation.
    assert probe(workdir/'top5-work/part-1.mov').has_audio
    print('RENDER_ARTIFACT='+str((workdir/'clips/top5.mp4').resolve()))


def test_download_error_drops_the_ytdlp_boilerplate():
    raw=('ERROR: [TikTok] 7000000000000000001: Unexpected response from webpage request; '
         'please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out '
         'the appropriate issue template. Confirm you are on the latest version using  yt-dlp -U')
    clean=t5.readable_download_error(Exception(raw))
    assert clean=='[TikTok] 7000000000000000001: Unexpected response from webpage request'
    assert 'github' not in clean and 'yt-dlp -U' not in clean
    assert t5.readable_download_error(Exception('ERROR: Video is private'))=='Video is private'


def test_tiktok_download_retries_transient_extraction_failures(tmp_path,monkeypatch):
    import yt_dlp
    from clipforge import montage
    calls=[]
    class FlakyYDL:
        def __init__(self,opts): self.opts=opts
        def __enter__(self): return self
        def __exit__(self,*_): return False
        def extract_info(self,url,download):
            calls.append(url)
            if len(calls)<3:
                raise yt_dlp.utils.DownloadError('ERROR: [TikTok] 1: Unable to extract universal data for rehydration')
            path=tmp_path/'1.mp4';path.write_bytes(b'x')
            return {'id':'1','ext':'mp4','requested_downloads':[{'filepath':str(path)}]}
        def prepare_filename(self,info): return str(tmp_path/'1.mp4')
    monkeypatch.setattr(yt_dlp,'YoutubeDL',FlakyYDL)
    monkeypatch.setattr(montage,'TIKTOK_RETRY_DELAY',0)
    assert montage.download_tiktok('https://www.tiktok.com/@x/video/1',tmp_path)==tmp_path/'1.mp4'
    assert len(calls)==3
    class PrivateYDL(FlakyYDL):
        def extract_info(self,url,download):
            calls.append(url);raise yt_dlp.utils.DownloadError('ERROR: Video is private')
    calls.clear();monkeypatch.setattr(yt_dlp,'YoutubeDL',PrivateYDL)
    with pytest.raises(montage.DownloadError,match='Video is private'):
        montage.download_tiktok('https://www.tiktok.com/@x/video/1',tmp_path)
    assert len(calls)==1


def test_duration_out_of_bounds(workdir,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(t5,'probe',lambda p:SimpleNamespace(duration=1))
    value=payload();value['entries'][0]['start']=2
    with pytest.raises(ValueError,match='Vídeo 1'):
        t5.render_topfive(t5.TopFive.model_validate(value),[Path('x')]*5,workdir)


def test_clips_are_automatic_up_to_15s_and_never_pass_the_video_end(workdir,monkeypatch):
    from types import SimpleNamespace
    value=payload()
    value['entries'][0]['duration']=15.01
    with pytest.raises(ValidationError):t5.TopFive.model_validate(value)
    lengths=[40,8,40,12,40]
    value=payload()
    value['entries'][1].update(start=2)           # automático, mas só restam 6s
    value['entries'][2].update(start=3,duration=4)  # usuário diminuiu
    value['entries'][3].update(start=5,duration=15) # pedido passa do fim (12s)
    sources=[workdir/f'v{i}' for i in range(5)]
    for source in sources:source.write_bytes(b'x')
    monkeypatch.setattr(t5,'probe',lambda p:SimpleNamespace(duration=lengths[int(p.name[1])],has_audio=True,width=1080,height=1920))
    monkeypatch.setattr(t5,'ffmpeg',lambda *a,**k:None)
    class Captured(Exception):pass
    def capture(spec,timeline,path):raise Captured(timeline)
    monkeypatch.setattr(t5,'ranking_ass',capture)
    with pytest.raises(Captured) as caught:
        t5.render_topfive(t5.TopFive.model_validate(value),sources,workdir)
    assert [s['duration'] for s in caught.value.args[0]]==[15,6,4,7,15]


def test_probe_endpoint_reads_duration(client,monkeypatch):
    from clipforge.download import DownloadError
    monkeypatch.setattr(t5,'tiktok_duration',lambda url:42.5)
    response=client.post('/api/top5/probe',json={'url':'https://www.tiktok.com/@x/video/1'})
    assert response.status_code==200 and response.json()=={'duration':42.5,'max_clip':15}
    assert client.post('/api/top5/probe',json={'url':'https://www.tiktok.com/@x'}).status_code==422
    def fail(url):raise DownloadError('Não foi possível ler o vídeo do TikTok')
    monkeypatch.setattr(t5,'tiktok_duration',fail)
    response=client.post('/api/top5/probe',json={'url':'https://www.tiktok.com/@x/video/1'})
    assert response.status_code==502 and 'ler o vídeo' in response.json()['detail']


def test_failed_download_keeps_going_and_retry_only_fetches_what_is_missing(workdir,monkeypatch):
    calls=[]
    def fake_download(url,folder):
        calls.append(url)
        if url.endswith('02'):raise t5.DownloadError('bloqueado')
        folder.mkdir(parents=True,exist_ok=True);path=folder/(url[-2:]+'.mp4');path.write_bytes(b'x');return path
    monkeypatch.setattr(t5,'download_tiktok',fake_download)
    monkeypatch.setattr(t5,'render_topfive',lambda spec,sources,directory,progress:{'sources':sources})
    value=payload()
    with pytest.raises(t5.DownloadError) as caught:
        t5.process_topfive({'top5':value},workdir)
    assert str(caught.value)=='Vídeo 3 (Momento 3): bloqueado' and len(calls)==5
    from types import SimpleNamespace
    monkeypatch.setattr(t5,'probe',lambda p:SimpleNamespace(duration=12))
    status=t5.source_status({'top5':value},workdir)
    assert status[0]['duration']==12
    assert [s['downloaded'] for s in status]==[True,True,False,True,True]
    # A posição 3 ganha outro link: só ela é baixada, e o arquivo antigo não fica para trás.
    calls.clear();value['entries'][2]['url']='https://www.tiktok.com/@test/video/7000000000000000099'
    result=t5.process_topfive({'top5':value},workdir)
    assert calls==[value['entries'][2]['url']] and len(result['sources'])==5


def test_legacy_long_trims_are_shortened_when_loaded():
    value=payload();value['entries'][0]['duration']=40
    assert t5.load_spec(value).entries[0].duration==t5.MAX_CLIP


def test_edit_one_position_of_failed_ranking(client,workdir):
    job_id=client.post('/api/top5',json=payload()).json()['job_id']
    task=db.claim_pipeline_stage()
    fixed={'url':'https://www.tiktok.com/@test/video/7000000000000000077','name':'Novo','start':1,'duration':5}
    assert client.put(f'/api/top5/{job_id}/entries/3',json=fixed).status_code==409  # ainda processando
    db.fail_pipeline_stage(task,'ValueError: Vídeo 4 (Momento 4): início fora')
    assert client.put(f'/api/top5/{job_id}/entries/9',json=fixed).status_code==404
    assert client.put(f'/api/top5/{job_id}/entries/3',json={**fixed,'duration':20}).status_code==422
    response=client.put(f'/api/top5/{job_id}/entries/3',json=fixed)
    assert response.status_code==200
    job=db.get_job(job_id)
    assert job['status']=='queued' and job['error'] is None
    entries=job['settings']['top5']['entries']
    assert entries[3]==t5.Entry.model_validate(fixed).model_dump() and entries[0]==t5.Entry.model_validate(payload()['entries'][0]).model_dump()
    sources=client.get(f'/api/top5/{job_id}/sources').json()['sources']
    assert len(sources)==5 and not any(s['downloaded'] for s in sources)


def test_three_positions_and_custom_typography(workdir):
    value = payload()
    value.update(entries=value['entries'][:3], title_font='Georgia', rank_font='Impact',
                 text_effect='neon', accent_color='#ff3366', rank_position=50)
    spec = t5.TopFive.model_validate(value)
    path = workdir/'custom.ass'
    t5.ranking_ass(spec, [{'rank':3-i,'start':i,'end':i+1} for i in range(3)], path)
    text = path.read_text(encoding='utf-8')
    assert r'\fnGeorgia' in text and r'\fnImpact' in text
    assert '&H006633ff&' in text
    assert r'\pos(45,783)' in text
    assert r'\fad(180,0)' in text
    assert '}4.' not in text and '}5.' not in text
    for change in [dict(title_font='Unknown'), dict(accent_color='invalid'), dict(rank_position=100)]:
        with pytest.raises(ValidationError):
            t5.TopFive.model_validate({**value, **change})


def test_intro_name_animation_and_watermark(workdir):
    value=payload()
    spec=t5.TopFive.model_validate({**value,'watermark':'meuperfil','watermark_opacity':40})
    path=workdir/'animated.ass'
    timeline=[{'rank':i+1,'start':i,'end':i+1} for i in range(5)]
    t5.ranking_ass(spec,timeline,path)
    text=path.read_text(encoding='utf-8')
    assert spec.watermark=='@meuperfil'
    assert t5.TopFive.model_validate({**value,'watermark':'@Frieren-Hub'}).watermark=='@Frieren-Hub'
    assert r'\move(540,-420,540,115,0,550)' in text
    assert text.count(r'\move(-180,')==5
    assert text.count(r'\move(1150,')==5
    assert r'\alpha&H99&' in text and '@meuperfil' in text
    assert r'\pos(540,1640)' in text
    spec.animate_intro=False;spec.animate_reveal=False;spec.watermark=''
    t5.ranking_ass(spec,timeline,path)
    text=path.read_text(encoding='utf-8')
    assert r'\move(' not in text and '@meuperfil' not in text
    with pytest.raises(ValidationError):
        t5.TopFive.model_validate({**value,'watermark':'bad name'})


def test_trim_settings_roundtrip(client):
    value=payload()
    value['entries'][0].update(start=5,duration=7)
    value.update(watermark='@perfil',animate_intro=True,watermark_opacity=35)
    response=client.post('/api/top5',json=value)
    assert response.status_code==202
    saved=db.get_job(response.json()['job_id'])['settings']['top5']
    assert saved['entries'][0]['start']==5 and saved['entries'][0]['duration']==7
    assert saved['watermark']=='@perfil' and saved['watermark_opacity']==35


def test_comic_palette_and_pop(workdir):
    spec=t5.TopFive.model_validate({**payload(),'title_font':'Comic Sans MS','rank_font':'Segoe Print',
        'rank_color':'#aabbcc','outline_color':'#112233','outline_width':9,
        'shadow_color':'#445566','shadow_depth':6,'animation_style':'pop'})
    path=workdir/'comic.ass'
    t5.ranking_ass(spec,[{'rank':i+1,'start':i,'end':i+1} for i in range(5)],path)
    text=path.read_text(encoding='utf-8')
    assert r'\fnComic Sans MS' in text and r'\fnSegoe Print' in text
    assert r'\bord9\3c&H00332211&\shad6\4c&H00665544&' in text
    assert '&H00ccbbaa&' in text and r'\fscx70\fscy70' in text
    assert r'\move(' not in text
    for change in [{'outline_width':13},{'shadow_depth':-1},{'outline_color':'red'},{'animation_style':'bad'}]:
        with pytest.raises(ValidationError):
            t5.TopFive.model_validate({**payload(),**change})
