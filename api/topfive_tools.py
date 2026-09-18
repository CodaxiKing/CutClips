"""Audio assets, factual export review, source history and manual publication reports."""
import json
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from api import db
from api.topfive import _topfive_job
from cutclips.config import STORAGE
from cutclips.topfive import Entry, audio_path, file_digest
from cutclips.probe import probe

router = APIRouter(prefix='/api/top5-tools', tags=['Top 5 review'])


@router.post('/audio')
def upload_audio(file: UploadFile = File(...)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.wav', '.mp3', '.m4a', '.ogg', '.flac', '.aac'}:
        raise HTTPException(422, 'Use WAV, MP3, M4A, OGG, FLAC ou AAC.')
    folder = Path(STORAGE) / 'top5-audio'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        source, output = Path(temp)/('input'+suffix), Path(temp)/'audio.wav'
        size = 0
        with source.open('wb') as stream:
            while chunk := file.file.read(1024*1024):
                size += len(chunk)
                if size > 25*1024*1024:
                    raise HTTPException(413, 'O limite por áudio é 25 MB.')
                stream.write(chunk)
        try:
            result = subprocess.run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe',
                '-i',str(source),'-map','0:a:0','-t','15','-vn','-ar','48000','-ac','2','-c:a','pcm_s16le',str(output)],
                capture_output=True, timeout=45)
            if result.returncode or not output.exists() or output.stat().st_size < 100:
                raise ValueError()
        except (ValueError, OSError, subprocess.TimeoutExpired):
            raise HTTPException(422, 'Não foi possível ler uma faixa de áudio válida neste arquivo.')
        asset = file_digest(output)
        output.replace(folder/(asset+'.wav'))
    return {'asset':asset, 'name':Path(file.filename).name, 'max_seconds':15}


@router.get('/audio/{asset}')
def preview_audio(asset: str):
    try:
        return FileResponse(audio_path(asset), media_type='audio/wav')
    except ValueError as exc:
        raise HTTPException(404, str(exc))


def source_key(url):
    u = urlparse(url)
    match = re.search(r'/video/(\d+)', u.path)
    return 'video:'+match[1] if match else (u.hostname or '')+u.path.rstrip('/')


class HistoryQuery(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=5)
    exclude_job: str = ''


def history(urls, exclude='', hashes=()):
    keys = {source_key(u) for u in urls}
    matches = []
    with db.connect() as c:
        rows = c.execute("SELECT id,title,status,settings,manifest FROM jobs WHERE json_extract(settings,'$.kind')='top5' AND id<>? ORDER BY created_at DESC", (exclude,)).fetchall()
    for row in rows:
        spec = json.loads(row['settings']).get('top5', {})
        timeline = (json.loads(row['manifest'] or '{}') or {}).get('timeline', [])
        for i, entry in enumerate(spec.get('entries', [])):
            segment = next((s for s in timeline if s.get('rank') == i+1), {})
            same_file = bool(segment.get('source_sha256') and segment['source_sha256'] in hashes)
            if source_key(entry['url']) in keys or same_file:
                matches.append({'job_id':row['id'],'title':row['title'],'status':row['status'],
                    'position':i+1,'url':entry['url'],'match':'arquivo idêntico' if same_file else 'link / ID do vídeo'})
    return matches


@router.post('/history')
def source_history(data: HistoryQuery):
    for url in data.urls:
        try:
            Entry.tiktok_url(url)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    return {'matches':history(data.urls, data.exclude_job),
            'note':'Histórico dos projetos existentes. Links curtos diferentes só podem ser comparados pelo arquivo após a montagem; arquivos recodificados podem não coincidir.'}


@router.get('/{job_id}/review')
def review(job_id: str):
    job, directory = _topfive_job(job_id)
    entries = job['settings']['top5']['entries']
    timeline = (job['manifest'] or {}).get('timeline', [])
    warnings = []
    technical = None
    try:
        info = probe(directory/'clips'/'top5.mp4')
        technical = {'duration':info.duration,'width':info.width,'height':info.height,'fps':info.fps,'has_audio':info.has_audio}
        if info.width != 1080 or info.height != 1920:
            warnings.append('O arquivo não está na resolução vertical 1080 × 1920.')
        if not info.has_audio:
            warnings.append('O arquivo não possui faixa de áudio.')
        if info.duration > 60:
            warnings.append('Mais de 1 minuto: uma reivindicação ativa de Content ID pode bloquear um Short. Confira o YouTube Studio.')
    except Exception:
        warnings.append('Não foi possível verificar o MP4 final. Gere ou recupere o arquivo antes de exportar.')
    for i, entry in enumerate(entries):
        if entry.get('rights', 'unknown') in {'unknown','unlicensed'}:
            warnings.append(f'Posição {i+1}: autorização não confirmada para vídeo e áudio.')
        segment = next((s for s in timeline if s.get('rank') == i+1), {})
        if segment.get('source_height', 1920) < 720:
            warnings.append(f'Posição {i+1}: fonte com menos de 720 pixels de altura; ampliar não recupera detalhes.')
        if entry.get('audio_mode') == 'mute' and not entry.get('narration_asset'):
            warnings.append(f'Posição {i+1}: trecho intencionalmente silencioso.')
    hashes = [s['source_sha256'] for s in timeline if s.get('source_sha256')]
    return {'technical':technical,'entries':entries,'warnings':warnings,
            'history':history([e['url'] for e in entries],job_id,hashes),
            'note':'Autorizações são declarações suas. Esta revisão não consulta Content ID, não confirma licenças e não prevê alcance.'}


class RightsUpdate(BaseModel):
    rights: Literal['unknown','own','authorized','unlicensed']
    rights_notes: str = Field(default='', max_length=500)


@router.put('/{job_id}/rights/{index}')
def update_rights(job_id: str, index: int, data: RightsUpdate):
    job, _ = _topfive_job(job_id)
    if job['status'] != 'done':
        raise HTTPException(409, 'Aguarde a montagem terminar para atualizar a revisão.')
    if not 0 <= index < len(job['settings']['top5']['entries']):
        raise HTTPException(404, 'Posição não encontrada')
    settings = job['settings']
    settings['top5']['entries'][index].update(data.model_dump())
    db.update_settings(job_id,settings)
    return {'ok':True}


class PublicationReport(BaseModel):
    date: date
    video_url: str = Field(default='', max_length=600)
    restriction: Literal['unknown','none','claim_no_impact','blocked','age','other'] = 'unknown'
    views: int | None = Field(default=None, ge=0)
    feed_views: int | None = Field(default=None, ge=0)
    shown_in_feed: int | None = Field(default=None, ge=0)
    average_percentage: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    stayed_percentage: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    notes: str = Field(default='', max_length=2000)

    @field_validator('video_url')
    @classmethod
    def youtube_url(cls, value):
        if not value:
            return value
        u=urlparse(value)
        if u.scheme!='https' or u.hostname not in {'youtube.com','www.youtube.com','m.youtube.com','youtu.be'} or u.username or u.password or u.port:
            raise ValueError('Use um link HTTPS do YouTube')
        return value


@router.get('/{job_id}/reports')
def reports(job_id: str):
    _topfive_job(job_id)
    with db.connect() as c:
        rows=c.execute('SELECT data FROM topfive_reports WHERE job_id=? ORDER BY date DESC',(job_id,)).fetchall()
    return {'reports':[json.loads(r['data']) for r in rows]}


@router.put('/{job_id}/reports')
def save_report(job_id: str, data: PublicationReport):
    job,_ = _topfive_job(job_id)
    if job['status']!='done':
        raise HTTPException(409,'Registre resultados depois de concluir a montagem.')
    if data.views is not None and data.feed_views is not None and data.feed_views > data.views:
        raise HTTPException(422,'Visualizações do feed não podem superar as visualizações totais no mesmo período.')
    with db.connect() as c:
        c.execute('INSERT INTO topfive_reports VALUES(?,?,?) ON CONFLICT(job_id,date) DO UPDATE SET data=excluded.data',
                  (job_id,data.date.isoformat(),data.model_dump_json()))
    return {'ok':True}
