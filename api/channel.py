"""Public channel sample and explicitly user-supplied Studio diagnostics."""
import json
import re
import subprocess
import sys
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/channel", tags=["channel"])


class ChannelRequest(BaseModel):
    url: str = Field(max_length=500)
    format: str = "videos"

    @field_validator("url")
    @classmethod
    def channel_url(cls, value):
        u = urlparse(value.strip())
        if (u.scheme != "https" or u.hostname not in {"youtube.com", "www.youtube.com"}
                or u.username or u.password or u.port or u.query or u.fragment
                or not re.fullmatch(r"/(?:@[\w.\-]+|channel/UC[\w-]{22}|c/[\w.-]+|user/[\w.-]+)/?", u.path)):
            raise ValueError("Use o link HTTPS do canal: youtube.com/@nome ou /channel/ID, sem parâmetros.")
        return "https://www.youtube.com" + u.path.rstrip("/")

    @field_validator("format")
    @classmethod
    def valid_format(cls, value):
        if value not in {"videos", "shorts"}:
            raise ValueError("Escolha videos ou shorts")
        return value


@router.post("/inspect")
def inspect_channel(data: ChannelRequest):
    try:
        result = subprocess.run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--flat-playlist",
            "--dump-single-json", "--skip-download", "--playlist-end", "20", "--socket-timeout", "12",
            "--retries", "1", data.url + "/" + data.format], capture_output=True, text=True,
            encoding="utf-8", timeout=60)
        if result.returncode:
            raise HTTPException(502, "YouTube não disponibilizou os dados. Confira o canal e tente novamente; pode haver bloqueio ou limitação temporária.")
        raw = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "A consulta demorou demais. Tente novamente.")
    except (ValueError, OSError):
        raise HTTPException(502, "Não foi possível ler os dados públicos do canal.")
    videos = [{"title": x.get("title") or "Sem título", "views": x.get("view_count"),
               "duration": x.get("duration"), "id": x.get("id")}
              for x in raw.get("entries", []) if x and re.fullmatch(r"[\w-]{11}", x.get("id") or "")]
    known = [v["views"] for v in videos if v["views"] is not None]
    titles = [v["title"].strip().casefold() for v in videos]
    recommendations = ["Compare vídeos do mesmo formato e com o mesmo tempo desde a publicação; visualizações acumuladas não medem entrega atual.",
        "Abra a curva de retenção no Studio: revise quedas no início, contexto ausente e trechos que repetem a mesma ideia.",
        "Teste uma promessa clara no título e entregue essa promessa no vídeo. Mantenha comentário, demonstração ou narrativa próprios."]
    if len(set(titles)) < len(titles):
        recommendations.insert(0, "Há títulos repetidos nesta amostra. Diferencie a proposta e o conteúdo de cada vídeo.")
    return {"name": raw.get("channel") or raw.get("uploader") or raw.get("title"), "url": data.url,
        "format": data.format, "videos": videos, "sample_views": sum(known) if known else None,
        "views_available": len(known), "recommendations": recommendations,
        "delivery": "Não verificável pelo link público. Impressões, origem do tráfego e retenção exigem dados do YouTube Studio.",
        "source": "YouTube público • amostra de até 20 vídeos • consulta atual, sem histórico"}


class Metrics(BaseModel):
    impressions: int | None = Field(default=None, ge=0)
    previous_impressions: int | None = Field(default=None, ge=0)
    ctr: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    previous_ctr: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    retention: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    previous_retention: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    recommended_views: int | None = Field(default=None, ge=0)


@router.post("/diagnose")
def diagnose(data: Metrics):
    findings = []
    if data.impressions is None:
        findings.append("Entrega por impressões: sem dados. Visualizações públicas não substituem impressões.")
    elif data.impressions == 0:
        findings.append("Você informou zero impressões registradas; isso não prova ausência de distribuição em todas as superfícies, como o feed de Shorts.")
    else:
        findings.append(f"Você informou {data.impressions:,} impressões: houve exposição nas superfícies contabilizadas pelo YouTube.")
    for key, label, advice in [("impressions", "Impressões", "Compare temas, demanda e origem do tráfego; queda não comprova penalização."),
        ("ctr", "CTR", "Teste título e miniatura fiéis ao conteúdo, comparando a mesma origem de tráfego."),
        ("retention", "Porcentagem média assistida", "Revise a abertura e as quedas da curva de retenção; compare durações semelhantes.")]:
        current, previous = getattr(data, key), getattr(data, "previous_" + key)
        if current is not None and previous is not None:
            direction = "caiu" if current < previous else "subiu" if current > previous else "ficou estável"
            findings.append(f"{label} {direction}: {previous:g} → {current:g}. " + (advice if current < previous else "Confirme a tendência em mais vídeos comparáveis."))
    if data.recommended_views is not None:
        findings.append(f"Visualizações informadas de recomendações: {data.recommended_views}. Confira a definição e as origens selecionadas no Studio; zero não demonstra bloqueio do canal.")
    return {"source": "Dados informados por você; não sincronizados com o YouTube", "findings": findings,
        "note": "Não existe CTR ou retenção universal que garanta recomendações. Compare períodos de mesma duração e o mesmo formato."}
