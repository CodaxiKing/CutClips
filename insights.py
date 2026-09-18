"""Potencial estimado, risco pelas diretrizes do YouTube e títulos de cada vídeo gerado.

Nada aqui é probabilidade: o YouTube não publica como decide alcance nem punição, e
um "37% de chance" seria um número inventado. O potencial é uma estimativa a partir
de sinais medidos; o risco é um nível com o motivo de cada alerta, para a pessoa
revisar antes de publicar.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import numpy as np

from .config import Config
from .diagnostics import log_exception

VERSION = 1

CATEGORIES = {
    "linguagem": "Linguagem",
    "conteudo": "Conteúdo sensível",
    "desinformacao": "Desinformação",
    "audio": "Direitos do áudio",
    "reuso": "Conteúdo reutilizado",
}
# Só estas exigem ler o sentido do texto; sem IA elas ficam como "não analisado".
AI_ONLY = ("conteudo", "desinformacao")
LEVELS = ("baixo", "medio", "alto")

# Palavrões fortes. Pelas diretrizes para anunciantes, limitam ou removem anúncios;
# raramente geram advertência. A lista é curta de propósito: falso positivo aqui
# vira alerta falso na tela.
PROFANITY = [
    "porra", "caralho", "merda", "puta", "puto", "foda", "foder", "fodido", "fodase",
    "buceta", "cacete", "arrombado", "desgraçado", "filho da puta", "pqp", "vsf", "fdp",
    "fuck", "fucking", "shit", "bitch", "asshole", "motherfucker", "cunt",
]
_PROFANITY_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in PROFANITY) + r")(?!\w)")

REVIEW_SYSTEM = """Você revisa vídeos curtos antes da publicação no YouTube.
Para cada vídeo, aponte SOMENTE riscos reais pelas Diretrizes da Comunidade do YouTube
(violência explícita, conteúdo sexual, discurso de ódio, assédio, atos perigosos,
drogas) e pelas diretrizes para anunciantes (palavrões, temas sensíveis), além de
desinformação sobre saúde ou eleições. Não invente riscos: texto neutro não tem risco.
Avalie também o engajamento de 0 a 5 (gancho, retenção, emoção) e sugira de 3 a 5
títulos fiéis ao conteúdo, sem caça-clique enganoso.
Responda só JSON, neste formato:
{"videos":[{"index":1,
  "risks":[{"category":"linguagem|conteudo|desinformacao","level":"medio|alto",
            "reason":"motivo curto em português","quote":"trecho literal do texto"}],
  "engagement":{"gancho":0,"retencao":0,"emocao":0},
  "titles":["título 1","título 2","título 3"]}]}
"quote" deve ser copiado literalmente do texto do vídeo. Omita a categoria se não houver risco."""


def annotate(manifest: dict, directory: Path, cfg: Config, *, settings: dict | None = None,
             origin: str | None = None, context: str = "") -> dict:
    """Acrescenta `insights` a cada clipe do manifesto. Nunca levanta exceção.

    `origin` vale para o corte de vídeo longo: "link" quando a fonte foi baixada.
    """
    clips = manifest.get("clips") or []
    try:
        ai, note = _ai_review(manifest, clips, cfg, context)
    except Exception as exc:  # noqa: BLE001 - a análise é auxiliar, o vídeo já existe
        log_exception("insights: revisão por IA", exc)
        ai, note = {}, "A revisão por IA falhou; o resultado abaixo é só da análise local."
    for clip in clips:
        try:
            clip["insights"] = _clip_insights(manifest, clip, Path(directory), settings or {},
                                              origin, ai.get(clip.get("index")), note)
        except Exception as exc:  # noqa: BLE001
            log_exception("insights: análise do clipe", exc)
    return manifest


def _clip_insights(manifest: dict, clip: dict, directory: Path, settings: dict,
                   origin: str | None, ai: dict | None, note: str | None) -> dict:
    kind = manifest.get("kind") or "clip"
    text = " ".join(str(clip.get(k) or "") for k in ("title", "text"))
    video = directory / "clips" / str(clip.get("file") or "")
    measured = _measured_hook(video)

    items = _language_risks(text) + _audio_risks(kind, manifest, settings, video) + \
        _reuse_risks(kind, manifest, settings, origin)
    if ai:
        items += ai.get("risks", [])
    else:
        # Sem nota, a IA rodou e só omitiu este vídeo; com nota, ela não rodou e a nota diz por quê.
        reason = ("Não analisado: a IA não devolveu avaliação deste vídeo." if note is None
                  else "Não analisado sem a revisão por IA.")
        items += [{"category": c, "level": None, "reason": reason} for c in AI_ONLY]
    potential, basis, engagement = _potential(kind, clip, ai, measured)
    return {
        "version": VERSION,
        "revision": clip.get("revision", 0),
        "potential": potential,
        "potential_basis": basis,
        "engagement": engagement,
        "risk": {"level": _overall(items), "items": _merge(items)},
        "titles": _titles(clip, ai),
        "analysis": "ia" if ai else "local",
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Potencial e engajamento
# --------------------------------------------------------------------------- #

def _measured_hook(video: Path) -> float | None:
    """Gancho audiovisual medido nos 2 primeiros segundos, de 0 a 5."""
    if not video.is_file():
        return None
    from .signals import opening_signals
    try:
        return float(opening_signals(video, 0, 2)["audiovisual_hook"])
    except Exception as exc:  # noqa: BLE001
        log_exception("insights: sinais de abertura", exc)
        return None


def _to100(value) -> int | None:
    return None if value is None else int(round(max(0.0, min(5.0, float(value))) * 20))


def _duration_fit(seconds: float | None) -> int:
    """Shorts vão até 3 minutos; os mais curtos costumam segurar melhor a atenção."""
    if not seconds:
        return 50
    return 100 if seconds <= 60 else 80 if seconds <= 90 else 60 if seconds <= 180 else 30


def _potential(kind: str, clip: dict, ai: dict | None, measured: float | None) -> tuple[int, str, dict]:
    criteria = clip.get("criteria") or {}
    if kind == "clip" and clip.get("score") is not None:
        # O corte já tem avaliação editorial (IA + critérios locais + gancho medido).
        engagement = {"gancho": _to100(criteria.get("gancho")), "retencao": _to100(criteria.get("retencao")),
                      "emocao": _to100(criteria.get("emocao_utilidade"))}
        return int(round(float(clip["score"]))), "avaliação editorial do trecho", engagement

    rated = (ai or {}).get("engagement") or {}
    engagement = {"gancho": _to100(rated.get("gancho", measured)), "retencao": _to100(rated.get("retencao")),
                  "emocao": _to100(rated.get("emocao"))}
    fit = _duration_fit(clip.get("actual_duration"))
    hook = _to100(measured)
    if rated:
        known = [v for v in engagement.values() if v is not None]
        ai_part = sum(known) / len(known) if known else 50
        score = .6 * ai_part + .25 * (hook if hook is not None else ai_part) + .15 * fit
        return int(round(score)), "avaliação da IA, gancho medido e duração", engagement
    score = .7 * (hook if hook is not None else 50) + .3 * fit
    return int(round(score)), "só gancho medido e duração (sem IA)", engagement


# --------------------------------------------------------------------------- #
# Riscos locais
# --------------------------------------------------------------------------- #

def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _language_risks(text: str) -> list[dict]:
    found = [m.group(1) for m in _PROFANITY_RE.finditer(_fold(text))]
    if not found:
        return []
    counts = {w: found.count(w) for w in dict.fromkeys(found)}
    listed = ", ".join(f"“{w}”" + (f" ({n}x)" if n > 1 else "") for w, n in counts.items())
    return [{"category": "linguagem", "level": "alto" if len(found) >= 3 else "medio",
             "reason": f"Palavrão: {listed}. Pelas diretrizes para anunciantes, linguagem forte pode "
                       "limitar ou remover os anúncios; raramente gera advertência."}]


def _audio_risks(kind: str, manifest: dict, settings: dict, video: Path) -> list[dict]:
    items: list[dict] = []
    if kind == "top5":
        for i, entry in enumerate((settings.get("top5") or {}).get("entries") or [], 1):
            if entry.get("audio_mode", "original") != "original" or not entry.get("audio_volume", 1):
                continue
            if entry.get("rights") in ("own", "authorized"):
                continue
            items.append({"category": "audio", "level": "alto",
                          "reason": f"Posição {i} usa o áudio original de um vídeo de terceiros, sem direitos "
                                    "declarados. Música e som alheios costumam gerar reivindicação de direitos autorais."})
    elif kind == "reaction":
        sides = (settings.get("reaction") or {})
        loud = [n for n in ("top", "bottom") if float((sides.get(n) or {}).get("volume", 1) or 0) > 0]
        if loud:
            items.append({"category": "audio", "level": "alto" if len(loud) == 2 else "medio",
                          "reason": "O áudio original dos vídeos de terceiros está na mistura. Se houver música, "
                                    "é provável uma reivindicação de direitos autorais."})
    if manifest.get("music"):
        items.append({"category": "audio", "level": "medio",
                      "reason": "Música de fundo enviada por você: confirme que tem licença para usá-la no YouTube."})
    stretch = _music_like_ratio(video)
    if stretch is not None and stretch >= .35:
        items.append({"category": "audio", "level": "medio",
                      "reason": f"{round(stretch * 100)}% do áudio tem som contínuo sem fala, o que sugere música. "
                                "Só o Content ID, depois do envio, confirma se há reivindicação."})
    return items


def _music_like_ratio(video: Path, limit: float = 300.0) -> float | None:
    """Fração do tempo com som audível e sem fala detectada (VAD do faster-whisper).

    Música tocando por baixo da fala não é separada: o número subestima, não inventa.
    """
    if not video.is_file():
        return None
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    try:
        audio = decode_audio(str(video), sampling_rate=16000)[: int(limit * 16000)]
    except Exception as exc:  # noqa: BLE001 - vídeo sem trilha de áudio, por exemplo
        log_exception("insights: leitura do áudio", exc)
        return None
    window = 8000  # meio segundo
    count = len(audio) // window
    if count < 4:
        return None
    frames = audio[: count * window].reshape(count, window)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    audible = rms > max(.01, float(np.percentile(rms, 90)) * .15)
    speech = np.zeros(count, dtype=bool)
    for seg in get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300)):
        speech[seg["start"] // window: seg["end"] // window + 1] = True
    return float(np.mean(audible & ~speech))


def _reuse_risks(kind: str, manifest: dict, settings: dict, origin: str | None) -> list[dict]:
    if kind == "top5":
        entries = (settings.get("top5") or {}).get("entries") or []
        unknown = sum(e.get("rights") not in ("own", "authorized") for e in entries)
        if not entries:
            return []
        return [{"category": "reuso", "level": "alto" if unknown else "medio",
                 "reason": "Compilação de vídeos de terceiros. Pela política de conteúdo reutilizado, costuma não "
                           "ser monetizável sem comentário ou edição que acrescentem algo próprio"
                           + (f"; {unknown} posição(ões) sem direitos declarados." if unknown else ".")}]
    if kind == "reaction":
        return [{"category": "reuso", "level": "medio",
                 "reason": "Usa vídeos de terceiros. Reação com comentário próprio tende a ser aceita; "
                           "sem comentário, conta como conteúdo reutilizado."}]
    if kind == "quiz" and (manifest.get("background") or {}).get("mode") == "link":
        return [{"category": "reuso", "level": "medio",
                 "reason": "O fundo vem de um vídeo por link. Se não for seu, confirme que pode reutilizá-lo."}]
    if kind == "clip" and origin == "link":
        return [{"category": "reuso", "level": "medio",
                 "reason": "O vídeo de origem veio de um link. Se não for do seu canal, confirme a autorização "
                           "antes de publicar os cortes."}]
    return []


# --------------------------------------------------------------------------- #
# Revisão por IA: uma chamada para todos os clipes do vídeo
# --------------------------------------------------------------------------- #

def _ai_review(manifest: dict, clips: list[dict], cfg: Config, context: str) -> tuple[dict, str | None]:
    if cfg.llm_provider == "heuristic":
        return {}, "Análise local: configure uma IA para avaliar conteúdo sensível, desinformação e títulos."
    if not clips:
        return {}, None
    from .select import PROVIDERS, ProviderError, _extract_json
    payload = {"format": manifest.get("kind") or "corte de vídeo longo", "context": context[:1500],
               "videos": [{"index": c.get("index"), "title": str(c.get("title") or "")[:200],
                           "text": str(c.get("text") or "")[:1500],
                           "duration": c.get("actual_duration")} for c in clips]}
    try:
        raw = PROVIDERS[cfg.llm_provider](REVIEW_SYSTEM, json.dumps(payload, ensure_ascii=False), cfg)
    except ProviderError as exc:
        return {}, f"Análise local: {exc}"
    data = _extract_json(raw) if raw else {}
    texts = {c.get("index"): _fold(" ".join(str(c.get(k) or "") for k in ("title", "text"))) for c in clips}
    return {v["index"]: v for v in (_clean_review(r, texts) for r in data.get("videos") or []) if v}, None


def _clean_review(raw: dict, texts: dict) -> dict | None:
    """Valida a resposta da IA: categoria e nível conhecidos, citação que existe de fato."""
    if not isinstance(raw, dict) or raw.get("index") not in texts:
        return None
    text = texts[raw["index"]]
    risks = []
    for r in raw.get("risks") or []:
        if not isinstance(r, dict) or r.get("category") not in ("linguagem", "conteudo", "desinformacao") \
                or r.get("level") not in ("medio", "alto") or not str(r.get("reason") or "").strip():
            continue
        quote = str(r.get("quote") or "").strip()
        risks.append({"category": r["category"], "level": r["level"], "reason": str(r["reason"])[:300],
                      # Citação que não está no texto é invenção: fica o motivo, sai a citação.
                      **({"quote": quote[:160]} if quote and _fold(quote) in text else {})})
    engagement = {}
    for key in ("gancho", "retencao", "emocao"):
        value = (raw.get("engagement") or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 5:
            engagement[key] = float(value)
    titles = [str(t).strip()[:100] for t in raw.get("titles") or [] if isinstance(t, str) and t.strip()]
    return {"index": raw["index"], "risks": risks, "engagement": engagement, "titles": titles[:5]}


# --------------------------------------------------------------------------- #
# Agregação
# --------------------------------------------------------------------------- #

def _merge(items: list[dict]) -> list[dict]:
    """Uma entrada por categoria, no nível mais alto, com todos os motivos."""
    merged: dict[str, dict] = {}
    for item in items:
        slot = merged.setdefault(item["category"], {"category": item["category"],
                                                    "label": CATEGORIES[item["category"]],
                                                    "level": None, "reasons": [], "quotes": []})
        if item.get("level") and (slot["level"] is None or LEVELS.index(item["level"]) > LEVELS.index(slot["level"])):
            slot["level"] = item["level"]
        if item["reason"] not in slot["reasons"]:
            slot["reasons"].append(item["reason"])
        if item.get("quote") and item["quote"] not in slot["quotes"]:
            slot["quotes"].append(item["quote"])
    # Categoria só com "não analisado" perde esse aviso se outra fonte a avaliou.
    for slot in merged.values():
        if slot["level"]:
            slot["reasons"] = [r for r in slot["reasons"] if not r.startswith("Não analisado")]
    order = list(CATEGORIES)
    return sorted(merged.values(), key=lambda s: (-(LEVELS.index(s["level"]) if s["level"] else -1),
                                                   order.index(s["category"])))


def _overall(items: list[dict]) -> str:
    levels = [LEVELS.index(i["level"]) for i in items if i.get("level")]
    return LEVELS[max(levels)] if levels else "baixo"


def _titles(clip: dict, ai: dict | None) -> list[str]:
    seen, titles = set(), []
    for title in [*(clip.get("title_options") or []), *((ai or {}).get("titles") or []), clip.get("title")]:
        key = _fold(str(title or "")).strip()
        if key and key not in seen:
            seen.add(key)
            titles.append(str(title).strip())
    return titles[:6]
