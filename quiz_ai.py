"""Perguntas de quiz geradas por IA, com revisão antes de chegar à tela.

Usa os mesmos provedores da seleção de clipes (Anthropic, OpenAI, Ollama). O
risco aqui é diferente do corte: um gabarito errado publicado vira correção nos
comentários. Por isso são duas chamadas — uma gera com folga, outra confere cada
resposta como revisor e derruba o que for duvidoso, ambíguo ou datado.

A saída passa pelo mesmo modelo de validação do formulário (`quiz.Question`), então
pergunta longa demais, alternativa repetida ou JSON torto nunca chegam ao render.
"""
from __future__ import annotations

import os
from dataclasses import replace

from .config import Config
from .diagnostics import log_exception
from .select import PROVIDERS, ProviderError, _extract_json

DIFFICULTY = {
    "facil": "fácil: fatos que a maioria das pessoas conhece da escola ou do dia a dia",
    "medio": "média: exige algum conhecimento, mas com resposta conhecida por quem gosta do tema",
    "dificil": "difícil: curiosidades específicas, ainda assim verificáveis em fonte de referência",
}

SYSTEM = """Você cria perguntas de quiz para vídeos curtos verticais (Shorts, TikTok, Reels).

Regras absolutas:
- Só fatos estáveis e verificáveis em enciclopédia. Nada que muda com o tempo
  (quem ocupa um cargo hoje, recordes recentes, rankings, preços, "atualmente").
- Uma única resposta correta, sem margem para "depende" ou "tecnicamente".
- Nada de números aproximados como resposta, a menos que a pergunta diga "aproximadamente".
- A pergunta precisa se entender sozinha, lida em 3 segundos: até 110 caracteres.
- Resposta curta: até 32 caracteres.
- Alternativas erradas: três, do mesmo tipo da certa (outra capital, outro ano
  próximo), plausíveis, todas diferentes entre si e da certa, até 32 caracteres cada.
- Sem perguntas repetidas nem quase iguais.
- Evite temas sensíveis: tragédias, política partidária, religião, saúde individual.

Devolva SOMENTE JSON válido, sem comentários e sem cercas de código:
{"questions": [{"question": "...", "answer": "...", "wrong": ["...", "...", "..."]}]}"""

USER_TEMPLATE = """Tema: {topic}
Idioma: português do Brasil
Dificuldade: {difficulty}
Quantidade: {count} perguntas
{avoid}"""

REVIEW_SYSTEM = """Você é revisor de fatos de um quiz que será publicado em vídeo.
Para cada item, verifique com rigor:
- "correct": a resposta indicada é de fato a correta?
- "unambiguous": existe só uma resposta aceitável, sem depender de interpretação ou data?
- "wrong_ok": nenhuma das alternativas erradas também é correta?
Na dúvida, marque false. É melhor descartar uma boa pergunta do que publicar um gabarito errado.

Devolva SOMENTE JSON válido:
{"results": [{"index": 1, "correct": true, "unambiguous": true, "wrong_ok": true, "note": "..."}]}"""

MAX_ROUNDS = 2


def ai_status(cfg: Config | None = None) -> dict:
    """Diz se dá para gerar agora, sem chamar o provedor."""
    cfg = cfg or Config()
    provider = cfg.llm_provider
    if provider not in PROVIDERS:
        return {"provider": provider, "model": "", "ready": False,
                "message": "Geração por IA desligada: defina CLIPFORGE_LLM_PROVIDER como anthropic, "
                           "openai ou ollama no .env."}
    key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    if key and not os.getenv(key, "").strip():
        return {"provider": provider, "model": cfg.resolved_model(), "ready": False,
                "message": f"Defina {key} no .env e reinicie o CutClips para gerar com IA."}
    return {"provider": provider, "model": cfg.resolved_model(), "ready": True, "message": ""}


def _call(system: str, user: str, cfg: Config) -> dict:
    try:
        raw = PROVIDERS[cfg.llm_provider](system, user, cfg)
    except ProviderError:
        raise
    except Exception as exc:  # rede, cota, modelo inexistente
        log_exception("quiz_ai", exc)
        raise ProviderError(f"A IA não respondeu ({type(exc).__name__}): {str(exc)[:240]}") from exc
    try:
        return _extract_json(raw)
    except ValueError as exc:
        raise ProviderError("A IA devolveu uma resposta fora do formato esperado. Tente de novo.") from exc


def _valid(items, style: str, seen: set[str]) -> list[dict]:
    from .quiz import CHOICE_MAX, Question
    kept = []
    for item in items if isinstance(items, list) else []:
        try:
            question = Question.model_validate({"question": item.get("question", ""),
                                                "answer": item.get("answer", ""),
                                                "wrong": list(item.get("wrong") or [])[:3]})
        except Exception:
            continue
        options = [question.answer, *question.wrong]
        if style == "choices" and (len(question.wrong) != 3 or not all(question.wrong)
                                   or any(len(o) > CHOICE_MAX for o in options)
                                   or len({o.casefold() for o in options}) != 4):
            continue
        key = " ".join(question.question.casefold().split()).rstrip("?")
        if key in seen:
            continue
        seen.add(key)
        kept.append(question.model_dump())
    return kept


def _review(items: list[dict], cfg: Config) -> list[dict]:
    listing = "\n".join(
        f"{i}. Pergunta: {q['question']}\n   Resposta: {q['answer']}\n   Erradas: {'; '.join(q['wrong'])}"
        for i, q in enumerate(items, 1))
    reviewer = replace(cfg, llm_model=cfg.resolved_stage_model("review"))
    data = _call(REVIEW_SYSTEM, listing, reviewer)
    approved = set()
    for result in data.get("results", []) if isinstance(data.get("results"), list) else []:
        if (isinstance(result, dict) and result.get("correct") is True
                and result.get("unambiguous") is True and result.get("wrong_ok", True) is True):
            try:
                approved.add(int(result.get("index")))
            except (TypeError, ValueError):
                continue
    return [q for i, q in enumerate(items, 1) if i in approved]


def generate_questions(topic: str, count: int, *, style: str = "open", difficulty: str = "medio",
                       avoid: list[str] | tuple = (), cfg: Config | None = None) -> dict:
    """Devolve `count` perguntas revisadas, ou menos se a IA não conseguir (mínimo 3)."""
    cfg = cfg or Config()
    status = ai_status(cfg)
    if not status["ready"]:
        raise ProviderError(status["message"])
    topic = " ".join(str(topic).split())[:120]
    if len(topic) < 2:
        raise ValueError("Informe o tema do quiz")
    count = max(3, min(10, int(count)))
    seen = {" ".join(q.casefold().split()).rstrip("?") for q in avoid}
    accepted: list[dict] = []
    warnings: list[str] = []
    for _ in range(MAX_ROUNDS):
        missing = count - len(accepted)
        if missing <= 0:
            break
        known = [*avoid, *(q["question"] for q in accepted)][-60:]
        user = USER_TEMPLATE.format(
            topic=topic, difficulty=DIFFICULTY.get(difficulty, DIFFICULTY["medio"]),
            # Pedir folga cobre o que a validação e a revisão vão derrubar.
            count=missing + 3,
            avoid=("Não repita nem reformule estas perguntas já usadas:\n- " + "\n- ".join(known)) if known else "")
        fresh = _valid(_call(SYSTEM, user, cfg).get("questions"), style, seen)
        if not fresh:
            continue
        try:
            fresh = _review(fresh, cfg)
        except ProviderError as exc:
            # Sem revisor, as perguntas ainda servem; o aviso vai para o projeto.
            message = "A revisão automática falhou; confira cada resposta com atenção."
            if message not in warnings:
                warnings.append(message)
            log_exception("quiz_ai.review", exc)
        accepted.extend(fresh[:missing])
    if len(accepted) < 3:
        raise ProviderError("A IA não produziu perguntas confiáveis o bastante sobre esse tema. "
                            "Tente um tema mais amplo ou outra dificuldade.")
    if len(accepted) < count:
        warnings.append(f"A IA entregou {len(accepted)} de {count} perguntas depois da revisão.")
    return {"questions": accepted, "provider": cfg.llm_provider, "model": cfg.resolved_model(),
            "warnings": warnings}
