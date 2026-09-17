import copy
import json
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from api import youtube as yt


@pytest.fixture
def setup(monkeypatch):
    vault = {}
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(yt, "load", lambda: copy.deepcopy(vault))
    def save(value):
        vault.clear()
        vault.update(copy.deepcopy(value))
    monkeypatch.setattr(yt, "save", save)
    yt.PENDING.clear()
    yt.CACHE.clear()
    app = FastAPI()
    app.include_router(yt.router)
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        yield client, vault
    yt.PENDING.clear()
    yt.CACHE.clear()


def configured(vault):
    vault["client"] = {"client_id": "test.apps.googleusercontent.com", "client_secret": "test-secret"}


class Response:
    def __init__(self, data=None, text=""):
        self.data, self.text = data, text
    def json(self):
        return self.data


def test_setup_required_and_no_secrets(setup):
    client, vault = setup
    assert client.get('/api/youtube/status').json()['configured'] is False
    assert client.post('/api/youtube/connect').status_code == 409
    assert client.get('/api/youtube/dashboard').status_code == 401
    configured(vault)
    data = client.get('/api/youtube/status').json()
    assert data['configured'] is True
    assert 'secret' not in json.dumps(data)


def test_configuration_validates_and_rejects_csrf(setup):
    client, vault = setup
    assert client.post('/api/youtube/configure', json={'content': '{}'}).status_code == 400
    body = {'content': json.dumps({'installed': {'client_id': 'a.apps.googleusercontent.com', 'client_secret': 'hidden'}})}
    assert client.post('/api/youtube/configure', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/youtube/configure', json=body).status_code == 200
    assert vault['client']['client_secret'] == 'hidden'


def test_oauth_uses_pkce_cookie_and_validates_state(setup):
    client, vault = setup
    configured(vault)
    result = client.post('/api/youtube/connect')
    query = parse_qs(urlparse(result.json()['url']).query)
    assert query['code_challenge_method'] == ['S256']
    assert query['access_type'] == ['offline']
    assert query['redirect_uri'] == ['http://127.0.0.1:8001/api/youtube/callback']
    assert 'HttpOnly' in result.headers['set-cookie']
    assert client.get('/api/youtube/callback?state=wrong&code=x').status_code == 400
    state = query['state'][0]
    client.cookies.clear()
    assert client.get('/api/youtube/callback', params={'state': state, 'code':'x'}).status_code == 400


def test_oauth_success_cross_site_callback_and_no_replay(setup, monkeypatch):
    client, vault = setup
    configured(vault)
    state = parse_qs(urlparse(client.post('/api/youtube/connect').json()['url']).query)['state'][0]
    def call(method, url, token=None, **kwargs):
        if url.endswith('/token'):
            assert kwargs['data']['code_verifier']
            return Response({'access_token': 'access', 'refresh_token':'refresh', 'expires_in':3600})
        return Response({'items':[{'id':'UCtest', 'snippet':{'title':'Canal de teste'}}]})
    monkeypatch.setattr(yt, 'call', call)
    response = client.get('/api/youtube/callback', params={'state':state, 'code':'valid'}, headers={'sec-fetch-site':'cross-site'}, follow_redirects=False)
    assert response.status_code == 303
    assert vault['channel']['id'] == 'UCtest'
    assert vault['token']['refresh_token'] == 'refresh'
    assert client.get('/api/youtube/callback', params={'state':state, 'code':'valid'}).status_code == 400


def test_expired_state_and_cancel(setup):
    client, vault = setup
    configured(vault)
    state = parse_qs(urlparse(client.post('/api/youtube/connect').json()['url']).query)['state'][0]
    yt.PENDING[state]['expires'] = 0
    assert client.get('/api/youtube/callback', params={'state':state, 'code':'x'}).status_code == 400
    state = parse_qs(urlparse(client.post('/api/youtube/connect').json()['url']).query)['state'][0]
    result = client.get('/api/youtube/callback', params={'state':state, 'error':'access_denied'}, follow_redirects=False)
    assert result.status_code == 303 and 'cancelled' in result.headers['location']
    assert 'token' not in vault


def test_refresh_preserves_refresh_token(setup, monkeypatch):
    client, vault = setup
    configured(vault)
    vault['token'] = {'access_token':'old', 'refresh_token':'keep', 'expires_at':0}
    monkeypatch.setattr(yt, 'call', lambda *a, **k: Response({'access_token':'new','expires_in':3600}))
    assert yt.access_token() == 'new'
    assert vault['token']['refresh_token'] == 'keep'


def test_weighted_ctr_and_missing_days():
    records = {'2026-09-01': {'impressions':100,'weighted_ctr':1000}, '2026-09-02': {'impressions':900,'weighted_ctr':1800}}
    result = yt.aggregate_reach(records, date(2026,9,1), date(2026,9,3))
    assert result == {'impressions':1000,'ctr':2.8,'days':2,'expected_days':3}
    assert yt.aggregate_reach({},date(2026,9,1),date(2026,9,3))['impressions'] is None


def test_reach_replaces_duplicate_reports_and_caches(setup, monkeypatch):
    _, vault = setup
    downloads = []
    def pages(token, url, key, **kwargs):
        if key == 'jobs':
            return [{'id':'job1','reportTypeId':'channel_reach_basic_a1'}]
        return [{'startTime':'2026-09-01T08:00:00Z','createTime':str(i),'downloadUrl':f'https://youtubereporting.googleapis.com/{i}'} for i in (1,2)]
    def call(method,url,*args,**kwargs):
        downloads.append(url)
        count = 100 if url.endswith('/1') else 200
        return Response(text=f'date,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n20260901,{count},5\n')
    monkeypatch.setattr(yt, 'pages', pages)
    monkeypatch.setattr(yt, 'call', call)
    result=yt.reach('token',date(2026,9,1),date(2026,9,2))
    assert result['2026-09-01']['impressions'] == 200
    yt.reach('token',date(2026,9,1),date(2026,9,2))
    assert len(downloads) == 2


def test_reach_pending_and_download_host_validation(setup, monkeypatch):
    monkeypatch.setattr(yt, 'pages', lambda *a, **k: [])
    monkeypatch.setattr(yt, 'call', lambda *a, **k: Response({'id':'new'}))
    assert yt.reach('token',date(2026,9,1),date(2026,9,2)) == {}
    def pages(token,url,key,**kwargs):
        return [{'id':'job','reportTypeId':'channel_reach_basic_a1'}] if key=='jobs' else [{'startTime':'2026-09-01','createTime':'new','downloadUrl':'https://evil.example/report'}]
    monkeypatch.setattr(yt, 'pages', pages)
    with pytest.raises(HTTPException) as error:
        yt.reach('token',date(2026,9,1),date(2026,9,2))
    assert error.value.status_code == 502


def test_empty_official_report_is_known_zero(setup, monkeypatch):
    def pages(token,url,key,**kwargs):
        return [{'id':'job','reportTypeId':'channel_reach_basic_a1'}] if key=='jobs' else [{'startTime':'2026-09-01T08:00:00Z','createTime':'new','downloadUrl':'https://youtubereporting.googleapis.com/report'}]
    monkeypatch.setattr(yt,'pages',pages)
    monkeypatch.setattr(yt,'call',lambda *a,**k: Response(text='date,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n'))
    records=yt.reach('token',date(2026,9,1),date(2026,9,2))
    result=yt.aggregate_reach(records,date(2026,9,1),date(2026,9,2))
    assert result['impressions'] == 0 and result['ctr'] is None and result['days'] == 1


def test_external_browser_stays_on_local_callback_origin(setup,monkeypatch):
    client,_=setup
    urls=[]
    monkeypatch.setattr(yt.webbrowser,'open',lambda url: urls.append(url) or True)
    assert client.post('/api/youtube/open-browser').status_code == 200
    assert urls == ['http://127.0.0.1:8001/?connect=youtube#/channel']
    assert client.post('/api/youtube/open-browser',headers={'Origin':'https://evil.example'}).status_code == 403


def test_dashboard_handles_pending_reach_and_caches(setup, monkeypatch):
    client, vault = setup
    vault['channel'] = {'id':'UCtest','name':'Test'}
    monkeypatch.setattr(yt, 'access_token', lambda: 'token')
    queries=[]
    def report(token,start,end,metrics,**params):
        queries.append((start,end,params))
        if params.get('dimensions'):
            return []
        return [{'views':10, 'estimatedMinutesWatched':100,'averageViewPercentage':45}]
    monkeypatch.setattr(yt,'report',report)
    monkeypatch.setattr(yt,'reach',lambda *a: {})
    result=client.get('/api/youtube/dashboard?days=7')
    assert result.status_code == 200
    body=result.json()
    assert body['reach']['impressions'] is None and body['warnings']
    assert (queries[0][1]-queries[0][0]).days == 6
    assert queries[1][1] < queries[0][0]
    client.get('/api/youtube/dashboard?days=7')
    assert len(queries) == 5
    assert client.get('/api/youtube/dashboard?days=100').status_code == 422


def test_disconnect_revokes_and_clears(setup, monkeypatch):
    client,vault=setup
    configured(vault)
    vault['token']={'access_token':'access','refresh_token':'refresh'}
    vault['reach']={'days':{}}
    calls=[]
    monkeypatch.setattr(yt,'call',lambda *a,**k: calls.append(k))
    assert client.post('/api/youtube/disconnect').status_code == 200
    assert calls[0]['data']['token'] == 'refresh'
    assert list(vault) == ['client']
