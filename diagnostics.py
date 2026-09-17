"""Transforma exceção em mensagem que diz o que fazer.

Um aviso que diz apenas `TypeError` não ajuda ninguém: foi exatamente assim que
uma chave de API vazia manteve a seleção por IA desligada sem que nada na tela
indicasse o motivo.

Dois canais, de propósito:

* `explain()` devolve texto curado, para o manifesto e a interface. Nunca repete
  a mensagem original da exceção — ela pode carregar trecho de prompt, caminho
  interno ou credencial embutida em URL, e o manifesto é compartilhado junto com
  os clipes.
* `log_exception()` despeja o detalhe completo no console do worker, que é local
  e é onde se investiga de verdade.
"""
from __future__ import annotations

import sys
import traceback

# Assinaturas reconhecíveis, na ordem em que são testadas. Casar por trecho da
# mensagem é frágil por natureza; por isso o caso não reconhecido continua
# tendo uma saída honesta em vez de um palpite.
_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("could not resolve authentication", "api key not set", "api_key not set",
      "não está definida", "no api key"),
     "falta a chave da API. Preencha ANTHROPIC_API_KEY (ou OPENAI_API_KEY) no .env e reinicie o worker."),
    (("authenticationerror", "invalid api key", "invalid_api_key", "401", "unauthorized"),
     "a chave da API foi recusada. Confira se ela é válida e está ativa."),
    (("permissiondenied", "403", "forbidden"),
     "a chave não tem permissão para o modelo configurado."),
    (("notfounderror", "404", "model not found", "does not exist"),
     "o modelo configurado não existe nessa conta. Revise CLIPFORGE_LLM_MODEL."),
    (("ratelimit", "429", "quota", "credit balance", "insufficient_quota"),
     "o limite ou o crédito da API se esgotou. Tente mais tarde ou revise o plano."),
    (("timeout", "timed out"),
     "a IA demorou demais para responder."),
    (("apiconnection", "connectionerror", "network", "unreachable", "ssl", "dns"),
     "não foi possível falar com a API. Confira a conexão e o proxy."),
    (("jsondecode", "expecting value", "resposta sem candidatos", "sem candidatos"),
     "a IA respondeu fora do formato esperado."),
)


def explain(exc: BaseException) -> str:
    """Frase curada e acionável sobre `exc`, segura para gravar no manifesto."""
    name = type(exc).__name__
    haystack = f"{name} {exc}".casefold()
    for needles, message in _PATTERNS:
        if any(needle in haystack for needle in needles):
            return message
    return (f"falha inesperada ({name}). O motivo completo está no log do worker, "
            "junto da hora em que o projeto foi processado.")


def log_exception(context: str, exc: BaseException) -> None:
    """Detalhe completo no console do worker — o lugar de investigar."""
    print(f"[clipforge] {context}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
