"""Vídeos do TikTok com mais visualizações por tópico, para preencher um ranking.

Fonte: a lista pública "Top videos" da Central de Criação do TikTok. Sem login ela
devolve só os 4 primeiros de cada consulta, então juntamos os dois períodos
disponíveis (7 e 30 dias) nas regiões que têm dados e ordenamos pelo total de
visualizações. A lista inclui conteúdo impulsionado; as visualizações orgânicas
ajudam a separar o que foi alcance pago. O resultado fica em memória por alguns minutos.

A Central não tem categoria de dança nem vídeos por hashtag sem login. "Todos os
tópicos" junta as listas de todas as categorias, e "Dance" filtra essa junção pela
legenda. As hashtags em alta vêm da lista pública de hashtags, só como referência.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/trending", tags=["Descobrir"])

LABELS = {
    "11004": "Música e entretenimento",
    "11007": "Esportes",
    "11005": "Games",
    "11001": "Natureza e animais",
    "11010": "Comidas e bebidas",
    "11008": "Criatividade e talento",
    "11009": "Família e relacionamentos",
    "11013": "Estilo de vida e lazer",
    "11011": "Vlog e selfie",
    "11002": "Beleza e cuidados",
    "11003": "Moda",
    "11014": "Veículos e transporte",
    "11015": "Tecnologia e finanças",
    "11012": "Sociedade",
}
TOPICS = {"": "Todos os tópicos", "dance": "Dance", **LABELS}
# Regiões com dados públicos. Os EUA são servidos por outro domínio.
REGIONS = {"US": "Estados Unidos", "JP": "Japão", "ID": "Indonésia", "TH": "Tailândia", "VN": "Vietnã"}
PERIODS = (3, 5)  # criados nos últimos 7 e 30 dias
CACHE_SECONDS = 30 * 60
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
_ADS = re.compile(r"#(?:ad|ads|sponsored|publi|parceria|\w*partner)\b|\bpaid partnership\b", re.IGNORECASE)
# Dança pela legenda, em vários idiomas. "seedance" é um modelo de IA, não dança.
_DANCE = re.compile(r"(?<![^\W\d_])(?:danc|dança|danç|coreograf|choreo|tiktokdance)|#\w*(?<!see)dance\w*"
                    r"|ダンス|踊って|踊り|เต้น|nhảy|\bjoget|\bgoyang", re.IGNORECASE)
_HASHTAG = re.compile(r"#([^\s#@,.;:!?()\[\]{}\"'“”<>|/\\]+)")
# Hashtags genéricas que aparecem em tudo e não ajudam a escolher um tema.
_NOISE = {"fyp", "fypシ", "fypage", "foryou", "foryoupage", "foryourpage", "fy", "fyi", "viral", "trending", "trend",
          "xyzbca", "parati", "paravoce", "pravoce", "tiktok", "capcut", "pr", "ad", "ads", "sponsored", "publi",
          "shorts", "explore", "explorepage", "vn", "th"}
HASHTAG_REGIONS = {"BR": "Brasil", "US": "Estados Unidos", "MX": "México", "JP": "Japão", "ID": "Indonésia"}
# Setores da lista de hashtags (a API exige o ID numérico). Não há setor de dança.
INDUSTRIES = {
    10000000000: "Educação", 11000000000: "Veículos e transporte", 12000000000: "Bebês, crianças e maternidade",
    13000000000: "Serviços financeiros", 14000000000: "Beleza e cuidados pessoais", 15000000000: "Tecnologia e eletrônicos",
    16000000000: "Eletrodomésticos", 17000000000: "Viagens", 18000000000: "Produtos para casa", 19000000000: "Pets",
    20000000000: "Apps", 21000000000: "Casa e reforma", 22000000000: "Roupas e acessórios",
    23000000000: "Notícias e entretenimento", 24000000000: "Serviços empresariais", 25000000000: "Games",
    26000000000: "Serviços do dia a dia", 27000000000: "Comidas e bebidas", 28000000000: "Esportes e ar livre",
    29000000000: "Saúde", 30000000000: "E-commerce",
}
# Categoria de vídeo -> setores de hashtag mais próximos.
TOPIC_INDUSTRIES = {
    "11004": (23000000000,), "11007": (28000000000,), "11005": (25000000000,), "11001": (19000000000,),
    "11010": (27000000000,), "11008": (23000000000, 20000000000), "11009": (12000000000,),
    "11013": (17000000000, 21000000000, 26000000000), "11011": (20000000000, 23000000000),
    "11002": (14000000000, 29000000000), "11003": (22000000000,), "11014": (11000000000,),
    "11015": (15000000000, 13000000000, 16000000000), "11012": (23000000000, 10000000000, 24000000000),
}
_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


class TrendingError(RuntimeError):
    pass


def _host(region: str) -> str:
    return "https://ads.us.tiktok.com" if region == "US" else "https://ads.tiktok.com"


def _get(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    response = client.get(url, params=params)
    response.raise_for_status()
    body = response.json()
    if (body.get("BaseResp") or {}).get("StatusCode", 0) != 0:
        raise TrendingError((body.get("BaseResp") or {}).get("StatusMessage") or "resposta recusada")
    return body


def _video(entity: dict, region: str) -> dict | None:
    info, author = entity.get("itemInfo") or {}, entity.get("itemAuthorInfo") or {}
    metrics = entity.get("itemMetrics") or {}
    handle, item = author.get("handlerName") or "", str(info.get("itemID") or "")
    if not (info.get("videoURL") and re.fullmatch(r"[\w.\-]{1,64}", handle) and item.isdigit()):
        return None
    covers = {c.get("format"): c.get("imageUrl") for c in info.get("coverURLList") or []}
    return {
        "id": item,
        "url": f"https://www.tiktok.com/@{handle}/video/{item}",
        "author": handle,
        "nickname": author.get("nickName") or handle,
        "title": (info.get("title") or "").strip(),
        "views": int(metrics.get("videoViewsLifeTime") or metrics.get("videoViews") or 0),
        "organic_views": int(metrics.get("organicVideoViewsLifeTime") or metrics.get("organicVideoViews") or 0),
        "cover": covers.get("jpeg") or info.get("coverURL") or "",
        "preview": info["videoURL"],
        "created": info.get("createTime"),
        "regions": [region],
        "tags": [t.get("contentLabelName") for t in entity.get("contentTags") or [] if t.get("contentLabelName")],
    }


def fetch_topic(topic: str) -> list[dict]:
    """Todas as regiões e períodos de um tópico, sem repetição, do mais visto ao menos visto."""
    with httpx.Client(headers=_HEADERS, timeout=20) as client:
        hosts = {_host(r) for r in REGIONS}
        ends = {}
        for host in hosts:
            try:
                ends[host] = _get(client, host + "/CreativeOne/Report/GetTopContentsOverview")["lastDailyEndTimestamp"]
            except (httpx.HTTPError, ValueError, KeyError, TrendingError):
                continue
        if not ends:
            raise TrendingError("A Central de Criação do TikTok não respondeu")

        def one(job):
            region, period = job
            host = _host(region)
            if host not in ends:
                return []
            try:
                body = _get(client, host + "/CreativeOne/Report/CreativeCenterGetTopContentsList", {
                    "contentLabelIDs": topic, "countryCode": region, "limit": 20, "orderByMetric": 1,
                    "organicOnly": "false", "page": 1, "periodDimension": period, "periodEndTimestamp": ends[host]})
            except (httpx.HTTPError, ValueError, TrendingError):
                return []
            return [v for v in (_video(e, region) for e in body.get("entityInfos") or []) if v]

        with ThreadPoolExecutor(6) as pool:
            found = [v for batch in pool.map(one, [(r, p) for r in REGIONS for p in PERIODS]) for v in batch]
    return _merge(found)


def _merge(videos: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for video in videos:
        known = merged.get(video["id"])
        if not known:
            merged[video["id"]] = dict(video)
            continue
        known["views"] = max(known["views"], video["views"])
        known["organic_views"] = max(known["organic_views"], video["organic_views"])
        known["regions"] = sorted(set(known["regions"]) | set(video["regions"]))
    return sorted(merged.values(), key=lambda v: v["views"], reverse=True)


def _remember(key: str, fetch, refresh: bool, empty_message: str):
    with _lock:
        hit = _cache.get(key)
    if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[0]
    found = fetch()
    if not found:
        if hit:
            return hit[1], hit[0]
        raise TrendingError(empty_message)
    now = time.time()
    with _lock:
        _cache[key] = (now, found)
    return found, now


def cached_topic(topic: str, refresh: bool = False) -> tuple[list[dict], float]:
    """Uma categoria da Central ("" é a lista geral, sem categoria)."""
    return _remember(topic, lambda: fetch_topic(topic), refresh, "Nenhum vídeo público encontrado para este tópico agora")


def all_videos(refresh: bool = False) -> tuple[list[dict], float]:
    """Lista geral e todas as categorias juntas, para buscar por dança ou hashtag."""
    def fetch():
        def one(label):
            try:
                return cached_topic(label, refresh)[0]
            except (TrendingError, httpx.HTTPError):
                return []
        with ThreadPoolExecutor(4) as pool:
            return _merge([v for batch in pool.map(one, ["", *LABELS]) for v in batch])
    return _remember("*", fetch, refresh, "A Central de Criação do TikTok não respondeu")


def hashtag_counts(videos: list[dict], limit: int = 20) -> list[dict]:
    counts: dict[str, int] = {}
    for video in videos:
        for tag in {t.lower().rstrip("-_") for t in _HASHTAG.findall(video["title"])}:
            if len(tag) > 1 and tag not in _NOISE and not _ADS.search("#" + tag):
                counts[tag] = counts.get(tag, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"name": name, "videos": count} for name, count in ranked[:limit]]


def fetch_hashtags(region: str) -> dict[str, list[dict]]:
    """Hashtags em alta por setor ("" = sem setor). Sem login cada consulta traz só as 3 primeiras,
    então pedimos cada setor nos três períodos (7, 30 e 120 dias)."""
    with httpx.Client(headers=_HEADERS, timeout=20) as client:
        def one(job):
            period, industry = job
            body = {"countryCode": region, "timeRange": period, "page": 1, "limit": 20}
            if industry:
                body["industryID"] = industry
            try:
                response = client.post("https://ads.tiktok.com/CreativeOne/KnowledgeAPI/GetHashtagList", json=body)
                response.raise_for_status()
                return industry, response.json().get("items") or []
            except (httpx.HTTPError, ValueError):
                return industry, []
        with ThreadPoolExecutor(8) as pool:
            found = list(pool.map(one, [(p, i) for p in (7, 30, 120) for i in (0, *INDUSTRIES)]))
    posts: dict[str, dict[str, int]] = {}
    for industry, items in found:
        bucket = posts.setdefault(str(industry or ""), {})
        for item in items:
            name = str(item.get("hashtagName") or "").strip().lstrip("#")
            if name and _HASHTAG.fullmatch("#" + name):
                bucket[name] = max(bucket.get(name, 0), int(item.get("publishCnt") or 0))
    ranked = {k: [{"name": n, "posts": c} for n, c in sorted(v.items(), key=lambda item: -item[1])] for k, v in posts.items()}
    return ranked if any(ranked.values()) else {}


def hashtags_for(by_industry: dict[str, list[dict]], topic: str) -> tuple[list[dict], list[str]]:
    """Junta os setores da categoria. "Todos" usa todos; Dance filtra os nomes de todos os setores."""
    if topic in TOPIC_INDUSTRIES:
        keys = [str(i) for i in TOPIC_INDUSTRIES[topic]]
        sectors = [INDUSTRIES[i] for i in TOPIC_INDUSTRIES[topic]]
    else:
        keys, sectors = list(by_industry), []
    posts: dict[str, int] = {}
    for key in keys:
        for item in by_industry.get(key, []):
            if topic == "dance" and not _DANCE.search("#" + item["name"]):
                continue
            posts[item["name"]] = max(posts.get(item["name"], 0), item["posts"])
    return [{"name": n, "posts": c} for n, c in sorted(posts.items(), key=lambda item: -item[1])], sectors


@router.get("/topics")
def topics():
    return {"topics": [{"id": k, "name": v} for k, v in TOPICS.items()],
            "regions": [{"id": k, "name": v} for k, v in REGIONS.items()],
            "hashtag_regions": [{"id": k, "name": v} for k, v in HASHTAG_REGIONS.items()]}


@router.get("/videos")
def videos(topic: str = Query("", max_length=8), region: str = Query("", max_length=2),
           sort: str = Query("views", pattern="^(views|organic)$"), hide_ads: bool = True, refresh: bool = False):
    if topic not in TOPICS:
        raise HTTPException(404, "tópico desconhecido")
    if region and region not in REGIONS:
        raise HTTPException(422, "região sem dados públicos")
    try:
        items, fetched = cached_topic(topic, refresh) if topic in LABELS else all_videos(refresh)
    except TrendingError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Não foi possível consultar o TikTok agora") from exc
    items = [v for v in items if (not region or region in v["regions"]) and not (hide_ads and _ADS.search(v["title"]))
             and (topic != "dance" or _DANCE.search(v["title"]))]
    if sort == "organic":
        items = sorted(items, key=lambda v: v["organic_views"], reverse=True)
    return {"topic": {"id": topic, "name": TOPICS[topic]}, "fetched_at": fetched, "videos": items,
            "hashtags": hashtag_counts(items)}


@router.get("/hashtags")
def hashtags(region: str = Query("BR", max_length=2), topic: str = Query("", max_length=8), refresh: bool = False):
    if region not in HASHTAG_REGIONS:
        raise HTTPException(422, "região sem hashtags públicas")
    if topic not in TOPICS:
        raise HTTPException(404, "tópico desconhecido")
    try:
        by_industry, fetched = _remember("#" + region, lambda: fetch_hashtags(region), refresh,
                                         "A Central de Criação não retornou hashtags agora")
    except TrendingError as exc:
        raise HTTPException(502, str(exc)) from exc
    items, sectors = hashtags_for(by_industry, topic)
    return {"region": {"id": region, "name": HASHTAG_REGIONS[region]}, "topic": {"id": topic, "name": TOPICS[topic]},
            "sectors": sectors, "fetched_at": fetched, "hashtags": items}
