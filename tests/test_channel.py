import json
import subprocess
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import channel


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(channel.router)
    with TestClient(app) as connection:
        yield connection


def test_rejects_non_channel_urls(client):
    for url in ['http://localhost/', 'https://youtube.com.evil.org/@test',
                'https://www.youtube.com/watch?v=abc', 'https://www.youtube.com/@test?x=1']:
        assert client.post('/api/channel/inspect', json={'url': url}).status_code == 422


def test_public_sample_keeps_missing_values(client, monkeypatch):
    def run(args, **kwargs):
        assert args[-1] == 'https://www.youtube.com/@test/shorts'
        assert kwargs['timeout'] == 60
        return SimpleNamespace(returncode=0, stdout=json.dumps({'channel': 'Test', 'entries': [
            {'id': 'abcdefghijk', 'title': 'Original', 'view_count': None},
            {'id': 'lmnopqrstuv', 'title': 'Original', 'view_count': 0}]}))
    monkeypatch.setattr(channel.subprocess, 'run', run)
    result = client.post('/api/channel/inspect', json={'url': 'https://youtube.com/@test', 'format': 'shorts'}).json()
    assert result['views_available'] == 1
    assert result['videos'][0]['views'] is None
    assert 'repetidos' in result['recommendations'][0]
    assert 'Não verificável' in result['delivery']


def test_timeout(client, monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired('yt-dlp', 60)
    monkeypatch.setattr(channel.subprocess, 'run', run)
    assert client.post('/api/channel/inspect', json={'url': 'https://youtube.com/@test'}).status_code == 504


def test_diagnosis_unknown_zero_and_decline(client):
    assert 'sem dados' in client.post('/api/channel/diagnose', json={}).json()['findings'][0]
    result = client.post('/api/channel/diagnose', json={'impressions': 0, 'ctr': 2, 'previous_ctr': 4}).json()
    assert 'não prova' in result['findings'][0]
    assert any('CTR caiu' in x for x in result['findings'])
    assert client.post('/api/channel/diagnose', json={'ctr': 101}).status_code == 422
    assert client.post('/api/channel/diagnose', json={'impressions': -1}).status_code == 422
