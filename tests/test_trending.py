import httpx
import pytest

from api import trending


def entity(item, handle, views, organic, title="Vídeo", region_tag=None):
    return {
        "itemInfo": {"itemID": item, "title": title, "videoURL": f"https://v.tiktokcdn.com/{item}.mp4",
                     "coverURLList": [{"format": "jpeg", "imageUrl": f"https://p.tiktokcdn.com/{item}.jpeg"}]},
        "itemAuthorInfo": {"handlerName": handle, "nickName": handle.title()},
        "itemMetrics": {"videoViewsLifeTime": views, "organicVideoViewsLifeTime": organic},
        "contentTags": [{"contentLabelID": 11007, "contentLabelName": region_tag}] if region_tag else None,
    }


@pytest.fixture
def creative_center(monkeypatch):
    """Simula a Central de Criação: cada região devolve a sua lista e o JP repete um vídeo dos EUA."""
    calls = []
    lists = {
        "US": [entity(1, "alpha", 900, 100, "Gol incrível #fyp"), entity(2, "brand", 800, 10, "#ad compre já")],
        "JP": [entity(1, "alpha", 950, 120), entity(3, "gamma", 300, 290, "Defesa 🧤")],
        "ID": [entity(4, "bad handle!", 5000, 5000)],
    }

    def handler(request: httpx.Request):
        calls.append(request.url)
        if request.url.path.endswith("GetTopContentsOverview"):
            return httpx.Response(200, json={"BaseResp": {"StatusCode": 0}, "lastDailyEndTimestamp": 1789430400})
        region = request.url.params["countryCode"]
        assert request.url.params["contentLabelIDs"] == "11007"
        assert (request.url.host == "ads.us.tiktok.com") == (region == "US")
        return httpx.Response(200, json={"BaseResp": {"StatusCode": 0}, "entityInfos": lists.get(region, [])})

    real = httpx.Client
    monkeypatch.setattr(trending.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(trending, "_cache", {})
    return calls


def test_topic_merges_regions_and_orders_by_views(client, creative_center):
    body = client.get("/api/trending/videos", params={"topic": "11007", "hide_ads": "false"}).json()
    videos = body["videos"]
    assert body["topic"]["name"] == "Esportes"
    assert [v["id"] for v in videos] == ["1", "2", "3"]  # handle inválido descartado
    first = videos[0]
    assert first["url"] == "https://www.tiktok.com/@alpha/video/1"
    assert first["views"] == 950 and first["organic_views"] == 120 and first["regions"] == ["JP", "US"]
    assert first["preview"].endswith("/1.mp4") and first["cover"].endswith("/1.jpeg")


def test_filters_sorting_and_cache(client, creative_center):
    hidden = client.get("/api/trending/videos", params={"topic": "11007"}).json()["videos"]
    assert [v["id"] for v in hidden] == ["1", "3"]
    requests = len(creative_center)
    organic = client.get("/api/trending/videos", params={"topic": "11007", "sort": "organic", "region": "JP"}).json()
    assert [v["id"] for v in organic["videos"]] == ["3", "1"]
    assert len(creative_center) == requests  # mesma consulta vem do cache
    client.get("/api/trending/videos", params={"topic": "11007", "refresh": "true"})
    assert len(creative_center) > requests


def test_rejects_unknown_topic_and_region(client, creative_center):
    assert client.get("/api/trending/videos", params={"topic": "999"}).status_code == 404
    assert client.get("/api/trending/videos", params={"topic": "11007", "region": "BR"}).status_code == 422
    topics = client.get("/api/trending/topics").json()
    assert {"id": "11007", "name": "Esportes"} in topics["topics"]
    assert not creative_center


def test_offline_source_is_a_readable_error(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("sem rede")

    real = httpx.Client
    monkeypatch.setattr(trending.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(trending, "_cache", {})
    response = client.get("/api/trending/videos", params={"topic": "11007"})
    assert response.status_code == 502
    assert "Central de Criação" in response.json()["detail"]


@pytest.fixture
def all_topics(monkeypatch):
    """Cada categoria devolve vídeos diferentes; a lista geral repete um deles."""
    by_label = {
        "": [entity(10, "geral", 700, 70, "Top geral #futebol #fyp")],
        "11007": [entity(10, "geral", 650, 60, "Top geral #futebol"), entity(11, "bola", 500, 50, "Golaço #futebol #Brasil")],
        "11004": [entity(12, "passos", 400, 40, "Coreografia nova #dancinha #fyp"),
                  entity(13, "ia", 900, 90, "Vídeo com #seedance25"), entity(14, "grupo", 300, 30, "ダンス部 #dance")],
    }
    hashtag_calls = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("GetTopContentsOverview"):
            return httpx.Response(200, json={"BaseResp": {"StatusCode": 0}, "lastDailyEndTimestamp": 1})
        if request.url.path.endswith("GetHashtagList"):
            body = __import__("json").loads(request.content)
            hashtag_calls.append(body)
            by_industry = {
                None: [{"hashtagName": "setembroamarelo", "publishCnt": 100 * body["timeRange"]}, {"hashtagName": "bad tag", "publishCnt": 9}],
                28000000000: [{"hashtagName": "libertadores", "publishCnt": 500}, {"hashtagName": "dancinhadogol", "publishCnt": 40}],
                23000000000: [{"hashtagName": "setembroamarelo", "publishCnt": 70}, {"hashtagName": "mudancas", "publishCnt": 30}],
            }
            items = by_industry.get(body.get("industryID"), [])
            return httpx.Response(200, json={"BaseResp": {"StatusCode": 0}, "items": items})
        label = request.url.params["contentLabelIDs"]
        return httpx.Response(200, json={"BaseResp": {"StatusCode": 0}, "entityInfos": by_label.get(label, [])})

    real = httpx.Client
    monkeypatch.setattr(trending.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(trending, "_cache", {})
    monkeypatch.setattr(trending, "_flat_entries", lambda url, limit: [])  # sem YouTube de verdade nos testes
    return hashtag_calls


def test_all_topics_and_dance_come_from_every_category(client, all_topics):
    everything = client.get("/api/trending/videos", params={"topic": ""}).json()
    assert [v["id"] for v in everything["videos"]] == ["13", "10", "11", "12", "14"]
    assert everything["hashtags"][0] == {"name": "futebol", "videos": 2}
    assert "fyp" not in {t["name"] for t in everything["hashtags"]}
    dance = client.get("/api/trending/videos", params={"topic": "dance"}).json()
    assert dance["topic"]["name"] == "Dance"
    assert [v["id"] for v in dance["videos"]] == ["12", "14"]  # seedance é IA, não dança
    assert {t["name"] for t in dance["hashtags"]} == {"dancinha", "dance"}


def test_trending_hashtags_follow_the_selected_category(client, all_topics):
    everything = client.get("/api/trending/hashtags", params={"region": "BR"}).json()
    assert everything["hashtags"][:2] == [{"name": "setembroamarelo", "posts": 12000}, {"name": "libertadores", "posts": 500}]
    assert all(isinstance(c.get("industryID", 0), int) for c in all_topics)  # a API ignora o setor enviado como texto
    assert {c["countryCode"] for c in all_topics} == {"BR"} and len(all_topics) == 3 * (len(trending.INDUSTRIES) + 1)
    calls = len(all_topics)
    sports = client.get("/api/trending/hashtags", params={"region": "BR", "topic": "11007"}).json()
    assert sports["sectors"] == ["Esportes e ar livre"]
    assert [t["name"] for t in sports["hashtags"]] == ["libertadores", "dancinhadogol"]
    dance = client.get("/api/trending/hashtags", params={"region": "BR", "topic": "dance"}).json()
    assert [t["name"] for t in dance["hashtags"]] == ["dancinhadogol"]  # "mudancas" não é dança
    assert len(all_topics) == calls  # categorias reaproveitam a mesma consulta
    assert client.get("/api/trending/hashtags", params={"region": "XX"}).status_code == 422
    assert client.get("/api/trending/hashtags", params={"topic": "999"}).status_code == 404


def shorts_entry(video, views, title=""):
    return {"id": video, "url": f"https://www.youtube.com/shorts/{video}", "title": title, "view_count": views,
            "thumbnails": [{"url": f"https://i.ytimg.com/vi/{video}/oardefault.jpg"}]}


@pytest.fixture
def shorts_feeds(monkeypatch):
    feeds = {
        "futebol": [shorts_entry("aaaaaaaaaaa", 900, "Golaço #futebol"), shorts_entry("bbbbbbbbbbb", 50, "#ad chuteira")],
        "brasil": [shorts_entry("aaaaaaaaaaa", 950), {"id": "UCchannel00", "url": "https://www.youtube.com/channel/UCchannel00"},
                   shorts_entry("ccccccccccc", 300, "Torcida #brasil")],
        "dancinha": [shorts_entry("ddddddddddd", 5000, "Passinho novo #dancinha")],
    }
    asked = []

    def flat(url, limit):
        asked.append(url)
        return feeds.get(url.split("/hashtag/")[1].split("/")[0], [])

    monkeypatch.setattr(trending, "_flat_entries", flat)
    monkeypatch.setattr(trending, "_cache", {})
    return asked


def test_search_uses_youtube_shorts_hashtags(client, shorts_feeds):
    assert trending.shorts_tags("#Futebol") == ["futebol"]
    assert trending.shorts_tags("futebol brasil") == ["futebolbrasil", "futebol", "brasil"]
    body = client.get("/api/trending/search", params={"q": "futebol brasil"}).json()
    assert body["topic"] == {"id": "search", "name": "“futebol brasil” no YouTube Shorts"}
    assert [v["id"] for v in body["videos"]] == ["yt-aaaaaaaaaaa", "yt-ccccccccccc"]  # sem canal, sem publi
    first = body["videos"][0]
    assert first["views"] == 950 and first["source"] == "youtube" and first["url"] == "https://www.youtube.com/shorts/aaaaaaaaaaa"
    assert first["preview"] == "/api/trending/shorts/aaaaaaaaaaa/preview"
    assert {"name": "futebol", "videos": 1} in body["hashtags"]
    asked = len(shorts_feeds)
    client.get("/api/trending/search", params={"q": "futebol brasil"})
    assert len(shorts_feeds) == asked
    assert client.get("/api/trending/search", params={"q": "###"}).status_code == 422


def test_dance_adds_youtube_shorts_to_tiktok(client, all_topics, shorts_feeds):
    body = client.get("/api/trending/videos", params={"topic": "dance"}).json()
    assert [v["id"] for v in body["videos"]] == ["yt-ddddddddddd", "12", "14"]
    assert {t for t in trending.SHORTS_DANCE} <= {u.split("/hashtag/")[1].split("/")[0] for u in shorts_feeds}


def test_shorts_preview_is_downloaded_once(client, tmp_path, monkeypatch):
    monkeypatch.setattr(trending, "STORAGE", tmp_path)
    downloads = []

    def fake(video, folder):
        downloads.append(video)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{video}.mp4").write_bytes(b"mp4")
        return folder / f"{video}.mp4"

    monkeypatch.setattr(trending, "download_preview", fake)
    for _ in range(2):
        response = client.get("/api/trending/shorts/aaaaaaaaaaa/preview")
        assert response.status_code == 200 and response.content == b"mp4"
    assert downloads == ["aaaaaaaaaaa"]
    assert client.get("/api/trending/shorts/..%2Fsecret/preview").status_code == 404
    monkeypatch.setattr(trending, "download_preview", lambda v, f: (_ for _ in ()).throw(RuntimeError("x")))
    assert client.get("/api/trending/shorts/bbbbbbbbbbb/preview").status_code == 502
