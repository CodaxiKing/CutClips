"""Owner-authorized YouTube analytics. Credentials never leave the backend."""
import base64
import csv
import ctypes
import hashlib
import io
import json
import os
import secrets
import threading
import time
import webbrowser
import math
from functools import wraps
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from cutclips.config import STORAGE

router = APIRouter(prefix="/api/youtube", tags=["YouTube"])
VAULT = Path(STORAGE) / "youtube-credentials.bin"
LOCK = threading.RLock()
PENDING = {}
CACHE = {}
SCOPE = "https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"
REPORTING = "https://youtubereporting.googleapis.com/v1"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)
    return wrapped


def protect(data: bytes, decrypt=False):
    if os.name != "nt":
        raise HTTPException(503, "O armazenamento seguro desta integração requer Windows.")
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise HTTPException(503, "Não foi possível acessar as credenciais protegidas do Windows.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def load():
    if not VAULT.exists():
        return {}
    return json.loads(protect(VAULT.read_bytes(), decrypt=True))


def save(data):
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    temporary = VAULT.with_suffix(".tmp")
    temporary.write_bytes(protect(json.dumps(data).encode()))
    temporary.replace(VAULT)


def config():
    stored = load().get("client", {})
    return {"client_id": os.getenv("YOUTUBE_CLIENT_ID") or stored.get("client_id"),
            "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET") or stored.get("client_secret"),
            "redirect_uris": stored.get("redirect_uris", [])}


def local_origin(request):
    if request.url.hostname not in {"127.0.0.1", "localhost"}:
        raise HTTPException(403, "Conexão disponível somente no aplicativo local.")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
        raise HTTPException(403, "Origem não autorizada.")
    return str(request.base_url).rstrip("/")


def call(method, url, token=None, **kwargs):
    try:
        response = httpx.request(method, url, headers={"Authorization": f"Bearer {token}"} if token else {},
                                 timeout=35, follow_redirects=False, **kwargs)
    except httpx.RequestError:
        raise HTTPException(502, "Não foi possível acessar o Google. Verifique sua conexão e tente novamente.")
    if response.status_code >= 300:
        if response.status_code == 401:
            raise HTTPException(401, "A conexão expirou. Conecte o YouTube novamente.")
        if response.status_code == 403:
            raise HTTPException(403, "O Google recusou a consulta. Verifique as permissões, a cota e se as APIs YouTube Data, Analytics e Reporting estão ativadas.")
        raise HTTPException(502, "O Google não concluiu a consulta. Confira a configuração e tente novamente.")
    return response


def access_token():
    with LOCK:
        stored = load()
        token = stored.get("token", {})
        if not token:
            raise HTTPException(401, "Conecte seu canal para sincronizar as métricas.")
        if token.get("expires_at", 0) > time.time() + 90:
            return token["access_token"]
        if not token.get("refresh_token"):
            raise HTTPException(401, "Conecte novamente para renovar o acesso ao canal.")
        client = config()
        refreshed = call("POST", "https://oauth2.googleapis.com/token", data={
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token"}).json()
        token.update(refreshed)
        token["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
        stored["token"] = token
        save(stored)
        return token["access_token"]


class ClientFile(BaseModel):
    content: str = Field(max_length=20000)


@router.post("/configure")
@serialized
def configure(request: Request, body: ClientFile):
    local_origin(request)
    try:
        parsed = json.loads(body.content)
        client = parsed.get("installed") or parsed.get("web") or {}
        if not client.get("client_id", "").endswith(".apps.googleusercontent.com") or not client.get("client_secret"):
            raise ValueError()
        client = {k: client[k] for k in ("client_id", "client_secret", "redirect_uris") if k in client}
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Selecione o JSON de um cliente OAuth do Google, tipo Aplicativo para computador ou Aplicativo Web.")
    with LOCK:
        save({"client": client})
        CACHE.clear()
        PENDING.clear()
    return {"configured": True}


@router.get("/status")
@serialized
def status(request: Request):
    origin = local_origin(request)
    stored = load()
    client = config()
    return {"configured": bool(client["client_id"] and client["client_secret"]),
            "connected": bool(stored.get("token")), "channel": stored.get("channel"),
            "callback": origin + "/api/youtube/callback"}


@router.post("/connect")
@serialized
def connect(request: Request):
    origin = local_origin(request)
    client = config()
    if not client["client_id"] or not client["client_secret"]:
        raise HTTPException(409, "Importe primeiro o arquivo de configuração OAuth do Google.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    callback = origin + "/api/youtube/callback"
    with LOCK:
        for old in list(PENDING):
            if PENDING[old]["expires"] < time.time():
                del PENDING[old]
        PENDING[state] = {"verifier": verifier, "callback": callback, "expires": time.time() + 600}
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": callback, "response_type": "code", "scope": SCOPE,
        "state": state, "access_type": "offline", "prompt": "consent select_account",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
        "code_challenge_method": "S256"})
    response = JSONResponse({"url": url})
    response.set_cookie("youtube_oauth_state", state, httponly=True, samesite="lax", max_age=600, path="/api/youtube")
    return response


@router.post("/open-browser")
def open_browser(request: Request):
    origin = local_origin(request)
    if not webbrowser.open(origin + "/?connect=youtube#/channel"):
        raise HTTPException(503, "Abra o CutClips no navegador padrão para conectar sua conta Google.")
    return {"opened": True}


@router.get("/callback")
@serialized
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if request.url.hostname not in {"127.0.0.1", "localhost"}:
        raise HTTPException(403, "Callback disponível somente no aplicativo local.")
    with LOCK:
        pending = PENDING.get(state)
        cookie = request.cookies.get("youtube_oauth_state", "")
        if not pending or not secrets.compare_digest(cookie, state) or pending["expires"] < time.time():
            raise HTTPException(400, "Conexão inválida ou expirada. Inicie novamente pelo CutClips.")
        del PENDING[state]
    destination = "/?youtube="
    if error or not code:
        response = RedirectResponse(destination + "cancelled#/channel", status_code=303)
    else:
        client = config()
        token = call("POST", "https://oauth2.googleapis.com/token", data={
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "grant_type": "authorization_code", "code": code, "redirect_uri": pending["callback"],
            "code_verifier": pending["verifier"]}).json()
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        identity = call("GET", "https://www.googleapis.com/youtube/v3/channels", token["access_token"],
                        params={"part": "snippet", "mine": "true"}).json().get("items", [])
        if not identity:
            raise HTTPException(400, "A conta selecionada não possui um canal acessível. Selecione a conta proprietária do canal.")
        with LOCK:
            stored = load()
            stored.update(token=token, channel={"id": identity[0]["id"], "name": identity[0]["snippet"]["title"]})
            stored.pop("reach", None)
            save(stored)
            CACHE.clear()
        response = RedirectResponse(destination + "connected#/channel", status_code=303)
    response.delete_cookie("youtube_oauth_state", path="/api/youtube")
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/disconnect")
@serialized
def disconnect(request: Request):
    local_origin(request)
    with LOCK:
        stored = load()
        token = stored.get("token", {})
        if token:
            call("POST", "https://oauth2.googleapis.com/revoke", data={"token": token.get("refresh_token") or token["access_token"]})
        save({"client": stored.get("client", {})})
        CACHE.clear()
        PENDING.clear()
    return {"connected": False}


def rows(data):
    names = [h["name"] for h in data.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in data.get("rows", [])]


def report(token, start, end, metrics, **params):
    return rows(call("GET", ANALYTICS, token, params={"ids": "channel==MINE", "startDate": str(start),
        "endDate": str(end), "metrics": metrics, **params}).json())


def pages(token, url, key, **params):
    result = []
    for _ in range(100):
        body = call("GET", url, token, params=params).json()
        result.extend(body.get(key, []))
        if not body.get("nextPageToken"):
            return result
        params["pageToken"] = body["nextPageToken"]
    raise HTTPException(502, "Há relatórios demais para esta consulta. Reduza o período.")


def aggregate_reach(records, start, end):
    selected = [v for k, v in records.items() if str(start) <= k <= str(end)]
    impressions = sum(v["impressions"] for v in selected)
    return {"impressions": impressions if selected else None,
            "ctr": sum(v["weighted_ctr"] for v in selected) / impressions if impressions else None,
            "days": len(selected), "expected_days": (end-start).days + 1}


def reach(token, start, end):
    with LOCK:
        stored = load()
        cache = stored.get("reach", {"days": {}})
    jobs = pages(token, REPORTING + "/jobs", "jobs")
    job = next((j for j in jobs if j["reportTypeId"] == "channel_reach_basic_a1" and not j.get("expireTime")), None)
    if not job:
        job = call("POST", REPORTING + "/jobs", token, json={"reportTypeId": "channel_reach_basic_a1", "name": "CutClips - alcance"}).json()
    reports = pages(token, REPORTING + "/jobs/" + job["id"] + "/reports", "reports",
                    startTimeAtOrAfter=str(start) + "T00:00:00Z", startTimeBefore=str(end + timedelta(days=1)) + "T00:00:00Z")
    # Replacements are processed oldest first; newer reports supersede the same day.
    for item in sorted(reports, key=lambda x: x.get("createTime", "")):
        day = item["startTime"][:10]
        if cache["days"].get(day, {}).get("created", "") >= item.get("createTime", ""):
            continue
        url = item["downloadUrl"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".googleapis.com") or parsed.username:
            raise HTTPException(502, "Endereço de relatório inesperado do Google.")
        response = call("GET", url, token)
        reader = csv.DictReader(io.StringIO(response.text))
        required = {"date", "video_thumbnail_impressions", "video_thumbnail_impressions_ctr"}
        if not required.issubset(reader.fieldnames or []):
            raise HTTPException(502, "O relatório de alcance não contém as colunas esperadas.")
        daily = {day: {"impressions": 0, "weighted_ctr": 0, "created": item.get("createTime", "")}}
        for row in reader:
            key = row["date"]
            key = f"{key[:4]}-{key[4:6]}-{key[6:]}" if len(key) == 8 else key
            value = daily.setdefault(key, {"impressions": 0, "weighted_ctr": 0, "created": item.get("createTime", "")})
            try:
                count = int(row["video_thumbnail_impressions"])
                ctr = float(row["video_thumbnail_impressions_ctr"] or 0) if count == 0 else float(row["video_thumbnail_impressions_ctr"])
                if count < 0 or not math.isfinite(ctr) or not 0 <= ctr <= 100:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(502, "O relatório de alcance contém valores inválidos; tente sincronizar novamente.")
            value["impressions"] += count
            value["weighted_ctr"] += count * ctr
        cache["days"].update(daily)
    cache["days"] = {k: v for k, v in cache["days"].items() if k >= str(end - timedelta(days=180))}
    with LOCK:
        stored = load()
        stored["reach"] = cache
        save(stored)
    return cache["days"]


@router.get("/dashboard")
@serialized
def dashboard(request: Request, days: int = Query(default=28, ge=7, le=90)):
    local_origin(request)
    with LOCK:
        if days in CACHE and time.time() - CACHE[days][0] < 300:
            return CACHE[days][1]
    token = access_token()
    # Analytics uses Pacific calendar dates. Exclude today's incomplete day.
    from zoneinfo import ZoneInfo
    from datetime import datetime
    end = datetime.now(ZoneInfo("America/Los_Angeles")).date() - timedelta(days=1)
    start = end - timedelta(days=days-1)
    previous_end, previous_start = start-timedelta(days=1), start-timedelta(days=days)
    metrics = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost,likes,comments,shares"
    current = report(token, start, end, metrics)
    previous = report(token, previous_start, previous_end, metrics)
    daily = report(token, start, end, "views,estimatedMinutesWatched", dimensions="day", sort="day")
    traffic = report(token, start, end, "views", dimensions="insightTrafficSourceType", sort="-views")
    top = report(token, start, end, "views,averageViewPercentage,estimatedMinutesWatched", dimensions="video", sort="-views", maxResults=10)
    if top:
        titles = call("GET", "https://www.googleapis.com/youtube/v3/videos", token,
            params={"part":"snippet", "id": ",".join(v["video"] for v in top)}).json().get("items", [])
        title_map = {v["id"]: v["snippet"]["title"] for v in titles}
        for v in top:
            v["title"] = title_map.get(v["video"], v["video"])
    warnings = []
    try:
        reach_days = reach(token, previous_start, end)
    except HTTPException as exc:
        reach_days = {}
        warnings.append("Alcance: " + str(exc.detail))
    reach_now, reach_before = aggregate_reach(reach_days, start, end), aggregate_reach(reach_days, previous_start, previous_end)
    if reach_now["days"] < days:
        warnings.append(f"Impressões e CTR: {reach_now['days']} de {days} dias disponíveis. O Google pode demorar até 48 horas para gerar os primeiros relatórios; a cobertura histórica depende da API.")
    recommendations = []
    curr, prev = (current or [{}])[0], (previous or [{}])[0]
    for metric, advice in [("averageViewPercentage", "A porcentagem assistida caiu. Compare durações e formatos semelhantes e revise os primeiros segundos dos vídeos."),
                           ("views", "As visualizações caíram. Verifique quais fontes de tráfego perderam participação e teste temas próximos dos vídeos que mais atraíram público.")]:
        if metric in curr and metric in prev and curr[metric] < prev[metric]:
            recommendations.append(advice)
    if reach_now["days"] == days == reach_before["days"] and reach_now["ctr"] is not None and reach_before["ctr"] is not None and reach_now["ctr"] < reach_before["ctr"]:
        recommendations.append("O CTR caiu. Teste uma promessa mais clara no título e na capa, considerando mudanças nas origens de tráfego.")
    if not recommendations:
        recommendations.append("Compare os temas dos vídeos mais assistidos e teste novas abordagens próprias. Sem queda identificada nos dados disponíveis; isso não garante crescimento.")
    result = {"channel": load().get("channel"), "start": str(start), "end": str(end), "days": days,
        "current": curr, "previous": prev, "daily": daily, "traffic": traffic, "top": top,
        "reach": reach_now, "previous_reach": reach_before, "warnings": warnings,
        "recommendations": recommendations, "synced_at": time.time(),
        "note": "Dados oficiais do canal conectado, todos os formatos. O YouTube pode revisar números e entregar dias recentes com atraso. Impressões de miniaturas não representam todas as exibições no feed de Shorts."}
    with LOCK:
        CACHE[days] = (time.time(), result)
    return result
