"""Banco local de perguntas para o quiz.

Responde a "quem escolhe a pergunta" sem custo: o sorteio sai daqui e a pessoa
edita o que quiser antes de montar. A geração por IA (`quiz_ai`) é a outra fonte.
Toda resposta foi escolhida para caber numa linha curta na tela e não ter disputa
de gabarito — pergunta com "depende" gera comentário irritado, não engajamento.

Cada item traz três alternativas erradas plausíveis, para o formato A/B/C/D. Erradas
absurdas entregam a resposta; erradas do mesmo tipo (outra capital, outro ano
próximo) mantêm o suspense.
"""
from __future__ import annotations

import random

THEMES: dict[str, dict] = {
    "geral": {"label": "Conhecimentos gerais", "questions": [
        ("Quantos minutos tem um dia inteiro?", "1.440", ("1.240", "1.340", "1.640")),
        ("Qual é o maior órgão do corpo humano?", "A pele", ("O fígado", "O intestino", "O pulmão")),
        ("Quantas cores tem o arco-íris?", "7", ("5", "6", "8")),
        ("Qual animal é conhecido como o rei da selva?", "O leão", ("O tigre", "O elefante", "O gorila")),
        ("Quantos lados tem um hexágono?", "6", ("5", "7", "8")),
        ("Em que ano o homem pisou na Lua pela primeira vez?", "1969", ("1965", "1971", "1975")),
        ("Quantas teclas tem um piano padrão?", "88", ("76", "82", "96")),
        ("Qual é o único mamífero capaz de voar?", "O morcego", ("O esquilo-voador", "O colugo", "O petauro")),
        ("Quantos jogadores de cada time ficam em campo no futebol?", "11", ("9", "10", "12")),
        ("Quem pintou a Mona Lisa?", "Leonardo da Vinci", ("Michelangelo", "Rafael", "Botticelli")),
        ("Quantos ossos tem o corpo humano adulto?", "206", ("186", "226", "306")),
        ("Qual é a língua mais falada do mundo como língua nativa?", "Mandarim", ("Inglês", "Espanhol", "Hindi")),
    ]},
    "geografia": {"label": "Geografia", "questions": [
        ("Qual é a capital da Austrália?", "Camberra", ("Sydney", "Melbourne", "Brisbane")),
        ("Qual é o maior oceano do planeta?", "Pacífico", ("Atlântico", "Índico", "Ártico")),
        ("Qual é o país mais populoso do mundo?", "Índia", ("China", "Estados Unidos", "Indonésia")),
        ("Em que continente fica o Egito?", "África", ("Ásia", "Europa", "Oceania")),
        ("Qual é a capital do Canadá?", "Ottawa", ("Toronto", "Vancouver", "Montreal")),
        ("Qual é o maior país do mundo em área?", "Rússia", ("Canadá", "China", "Estados Unidos")),
        ("Qual é a montanha mais alta do mundo?", "Everest", ("K2", "Aconcágua", "Kilimanjaro")),
        ("Qual é o menor país do mundo?", "Vaticano", ("Mônaco", "San Marino", "Liechtenstein")),
        ("Qual é a capital da Turquia?", "Ancara", ("Istambul", "Esmirna", "Antália")),
        ("Qual é o maior deserto quente do mundo?", "Saara", ("Kalahari", "Gobi", "Atacama")),
        ("Quantos países fazem fronteira com o Brasil?", "10", ("8", "9", "11")),
        ("Qual rio passa por Paris?", "Sena", ("Tâmisa", "Reno", "Danúbio")),
    ]},
    "ciencia": {"label": "Ciência", "questions": [
        ("Qual é o planeta mais próximo do Sol?", "Mercúrio", ("Vênus", "Marte", "Terra")),
        ("Qual é o símbolo químico do ouro?", "Au", ("Ag", "Go", "Or")),
        ("Qual gás as plantas absorvem na fotossíntese?", "Gás carbônico (CO₂)", ("Oxigênio", "Nitrogênio", "Hidrogênio")),
        ("Qual é o maior planeta do Sistema Solar?", "Júpiter", ("Saturno", "Netuno", "Urano")),
        ("A que temperatura a água ferve ao nível do mar?", "100 °C", ("90 °C", "110 °C", "120 °C")),
        ("Quantos corações tem um polvo?", "3", ("1", "2", "4")),
        ("Qual é o elemento mais abundante no universo?", "Hidrogênio", ("Hélio", "Oxigênio", "Carbono")),
        ("Qual é o símbolo químico do sódio?", "Na", ("So", "Sd", "S")),
        ("Qual planeta é conhecido como planeta vermelho?", "Marte", ("Vênus", "Júpiter", "Mercúrio")),
        ("Quantos cromossomos tem uma célula humana comum?", "46", ("23", "44", "48")),
        ("Qual é o osso mais longo do corpo humano?", "Fêmur", ("Tíbia", "Úmero", "Fíbula")),
        ("Qual é a velocidade aproximada da luz no vácuo?", "300 mil km/s", ("30 mil km/s", "150 mil km/s", "3 milhões km/s")),
    ]},
    "brasil": {"label": "Brasil", "questions": [
        ("Em que ano Brasília foi inaugurada?", "1960", ("1950", "1955", "1964")),
        ("Qual é o maior estado do Brasil em área?", "Amazonas", ("Pará", "Mato Grosso", "Minas Gerais")),
        ("Quantos estados tem o Brasil?", "26", ("24", "25", "27")),
        ("Qual é a capital de Santa Catarina?", "Florianópolis", ("Joinville", "Blumenau", "Chapecó")),
        ("Em que ano foi proclamada a Independência do Brasil?", "1822", ("1808", "1815", "1889")),
        ("Quantas estrelas tem a bandeira do Brasil?", "27", ("21", "26", "28")),
        ("Qual é o menor estado do Brasil em área?", "Sergipe", ("Alagoas", "Rio de Janeiro", "Espírito Santo")),
        ("Qual é a capital do Tocantins?", "Palmas", ("Araguaína", "Gurupi", "Porto Nacional")),
        ("Quantos títulos mundiais a seleção brasileira de futebol tem?", "5", ("4", "6", "3")),
        ("Qual é a ave símbolo do Brasil?", "Sabiá-laranjeira", ("Arara-azul", "Tucano", "Bem-te-vi")),
        ("Em que estado fica o arquipélago de Fernando de Noronha?", "Pernambuco", ("Rio Grande do Norte", "Paraíba", "Ceará")),
        ("Qual é o ponto mais alto do Brasil?", "Pico da Neblina", ("Pico da Bandeira", "Monte Roraima", "Pico 31 de Março")),
    ]},
}


def themes() -> list[dict]:
    return [{"id": key, "label": theme["label"], "size": len(theme["questions"])}
            for key, theme in THEMES.items()]


def _pool(theme: str) -> list[tuple]:
    if theme and theme not in THEMES:
        raise KeyError(theme)
    return [item for key, value in THEMES.items() if not theme or key == theme
            for item in value["questions"]]


def suggest(theme: str = "", count: int = 5, exclude: set[str] | None = None,
            rng: random.Random | None = None) -> list[dict]:
    """Sorteia perguntas sem repetir. Tema vazio mistura todos.

    `exclude` recebe perguntas já na tela, para que "trocar" traga outra de fato.
    """
    pool = _pool(theme)
    rng = rng or random
    skip = {text.casefold() for text in exclude or ()}
    fresh = [item for item in pool if item[0].casefold() not in skip]
    # Esgotado o tema, repetir é melhor do que devolver menos do que o pedido.
    chosen = rng.sample(fresh, min(count, len(fresh)))
    if len(chosen) < count:
        rest = [item for item in pool if item not in chosen]
        chosen += rng.sample(rest, min(count - len(chosen), len(rest)))
    return [{"question": q, "answer": a, "wrong": list(w)} for q, a, w in chosen]


def draw_batch(theme: str, videos: int, per_video: int,
               rng: random.Random | None = None) -> list[list[dict]]:
    """Distribui perguntas entre vários quizzes sem repetir nenhuma no lote."""
    pool = _pool(theme)
    needed = videos * per_video
    if needed > len(pool):
        raise ValueError(f"O banco {'deste tema ' if theme else ''}tem {len(pool)} perguntas; "
                         f"o lote pede {needed}. Reduza vídeos ou perguntas por vídeo, "
                         "ou gere com IA.")
    drawn = suggest(theme, needed, rng=rng)
    return [drawn[i * per_video:(i + 1) * per_video] for i in range(videos)]
