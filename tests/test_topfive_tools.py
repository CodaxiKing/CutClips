import io
import json
import subprocess
import wave

import numpy as np
import pytest
from api import db, studio, topfive_tools
from clipforge import config, topfive as t5


def payload():
    return {'headline':'Top 3', 'entries':[{'url':f'https://www.tiktok.com/@test/video/{i+1}', 'name':f'Item {i+1}', 'duration':1.5} for i in range(3)]}


def tone(hz):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes((np.sin(np.arange(96000)*2*np.pi*hz/48000)*9000).astype('<i2').tobytes())
    return out.getvalue()


@pytest.fixture
def assets(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'STORAGE',tmp_path)
    monkeypatch.setattr(topfive_tools,'STORAGE',tmp_path)


def upload(client,hz=880):
    result=client.post('/api/top5-tools/audio',files={'file':('tone.wav',tone(hz),'audio/wav')})
    assert result.status_code==200, result.text
    return result.json()['asset']


def test_audio_validation_and_missing_asset(client,assets):
    assert client.post('/api/top5-tools/audio',files={'file':('bad.wav',b'not audio')}).status_code==422
    data=payload();data['entries'][0]['audio_mode']='replace'
    assert client.post('/api/top5',json=data).status_code==422
    data['entries'][0]['audio_asset']='a'*64
    assert client.post('/api/top5',json=data).status_code==422
    data['entries'][0]['audio_asset']=upload(client)
    assert client.post('/api/top5',json=data).status_code==202
    assert client.get('/api/top5-tools/audio/'+'a'*64).status_code==404


def test_history_review_reports(client,tmp_path,assets):
    first=client.post('/api/top5',json=payload()).json()['job_id']
    result=client.post('/api/top5-tools/history',json={'urls':['https://m.tiktok.com/@other/video/1?tracking=x']}).json()
    assert result['matches'][0]['job_id']==first
    db.finish(first,{'timeline':[{'rank':1,'source_sha256':'a'*64}], 'clips':[]})
    assert topfive_tools.history([],hashes=['a'*64])[0]['match']=='arquivo idêntico'
    review=client.get(f'/api/top5-tools/{first}/review').json()
    assert review['technical'] is None and len(review['warnings'])>=4
    assert client.put(f'/api/top5-tools/{first}/rights/0',json={'rights':'authorized','rights_notes':'Licença de vídeo e áudio'}).status_code==200
    assert db.get_job(first)['settings']['top5']['entries'][0]['rights']=='authorized'
    report={'date':'2026-09-17','restriction':'claim_no_impact','views':5,'feed_views':0}
    assert client.put(f'/api/top5-tools/{first}/reports',json=report).status_code==200
    assert client.put(f'/api/top5-tools/{first}/reports',json={**report,'views':8}).status_code==200
    rows=client.get(f'/api/top5-tools/{first}/reports').json()['reports']
    assert len(rows)==1 and rows[0]['views']==8 and rows[0]['shown_in_feed'] is None
    assert client.put(f'/api/top5-tools/{first}/reports',json={**report,'feed_views':10}).status_code==422
    assert client.put(f'/api/top5-tools/{first}/reports',json={**report,'video_url':'javascript:alert(1)'}).status_code==422
    assert db.delete_job(first)
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM topfive_reports').fetchone()[0]==0


def samples(path):
    raw=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vn','-f','f32le','-ac','1','-ar','48000','pipe:1'],capture_output=True,check=True).stdout
    return np.frombuffer(raw,dtype='<f4')[12000:60000]


def strength(signal,hz):
    return abs(np.sum(signal*np.exp(-2j*np.pi*hz*np.arange(len(signal))/48000)))/len(signal)


def test_render_mute_replace_ducking_and_cache(client,tmp_path,assets):
    audio=upload(client,880);voice=upload(client,1320)
    source=tmp_path/'source.mp4'
    t5.ffmpeg(['-f','lavfi','-i','color=c=blue:size=180x320:rate=30:duration=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(source)],tmp_path)
    data=payload();data['normalize_audio']=False
    data['entries'][0]['audio_mode']='mute'
    data['entries'][1].update(audio_mode='replace',audio_asset=audio)
    data['entries'][2].update(audio_mode='replace',audio_asset=audio,narration_asset=voice,ducking=False)
    spec=t5.TopFive.model_validate(data)
    manifest=t5.render_topfive(spec,[source]*3,tmp_path,width=180,height=320)
    assert manifest['timeline'][0]['source_sha256']==t5.file_digest(source)
    assert np.max(np.abs(samples(tmp_path/'top5-work/part-0.mov')))<1e-6
    replaced=samples(tmp_path/'top5-work/part-1.mov')
    assert strength(replaced,880)>20*strength(replaced,440)
    before=samples(tmp_path/'top5-work/part-2.mov')
    spec.entries[2].ducking=True
    t5.render_topfive(spec,[source]*3,tmp_path,width=180,height=320)
    after=samples(tmp_path/'top5-work/part-2.mov')
    assert strength(after,880)<strength(before,880)*.6
    assert strength(after,1320)>strength(before,1320)*.9
    assert json.loads((tmp_path/'top5-work/part-2.json').read_text())['audio']['ducking'] is True
