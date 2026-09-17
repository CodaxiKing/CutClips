"""Quiz com suspense: pergunta, contagem regressiva, resposta.

O mecanismo é o mesmo do ranking do Top 5 — texto temporizado desenhado sobre o
vídeo —, só que o roteiro é outro. Cada pergunta ocupa um bloco fixo: ela aparece,
um contador desce na tela, e só então a resposta entra e fica até o fim do bloco.
A pergunta continua visível durante a resposta, senão o espectador que chegou no
meio não entende o que está lendo.

Dois estilos. Em "open" a resposta surge escrita. Em "choices" quatro alternativas
ficam na tela durante o suspense e, na revelação, a certa acende em verde e as
outras apagam — quem assiste escolhe uma letra antes, e é isso que vira comentário.

O fundo vem de três lugares: a pasta de fundos (escolha automática, ver
`backgrounds`), um link de vídeo ou uma cor lisa. Vídeo mais curto que o quiz
recomeça em loop. O som do fundo é opcional; a música enviada entra por cima.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import textwrap
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import backgrounds, sounds
from .captions import _ts
from .download import DownloadError, is_supported_url
from .editor import atomic_json
from .montage import (MUSIC_NAME, PLAY_H, PLAY_W, MusicOptions, add_music, ass_document, ffmpeg,
                      fetch_source, is_tiktok_url, music_path, normalize, text_literal)
from .probe import probe

# Posições no canvas de referência 1080x1920, medidas a partir do topo.
_Y_HEADLINE = 120
_Y_QUESTION = 560
_Y_COUNTDOWN = 1030
_Y_ANSWER = 1330
_Y_PROGRESS = 1760

# Estilo com alternativas: a pergunta sobe para liberar espaço aos quatro cartões.
_Y_CHOICE_QUESTION = 360
_CARD_X, _CARD_W, _CARD_H = 90, 900, 130
_CARD_TOP, _CARD_STEP = 700, 160
_Y_CHOICE_COUNTDOWN = 1450
LETTERS = "ABCD"
CHOICE_MAX = 32


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=120)
    answer: str = Field(min_length=1, max_length=60)
    # Alternativas erradas, usadas só no estilo "choices".
    wrong: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("question", "answer")
    @classmethod
    def one_line(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Preencha a pergunta e a resposta")
        return value

    @field_validator("wrong")
    @classmethod
    def tidy_wrong(cls, value):
        return [" ".join(str(item).split()) for item in value]


class Quiz(MusicOptions):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    headline: str = Field(min_length=1, max_length=60)
    questions: list[Question] = Field(min_length=3, max_length=10)
    style: Literal["open", "choices"] = "open"
    # Tempo de suspense e de resposta, por pergunta.
    suspense: float = Field(default=3.0, ge=1, le=15, allow_inf_nan=False)
    reveal: float = Field(default=2.0, ge=1, le=10, allow_inf_nan=False)
    countdown: bool = True
    # `None` vem de projetos antigos: vira "url" se houver link, senão "color".
    background: Literal["folder", "url", "color"] | None = None
    # Vídeo escolhido na pasta; vazio = automático.
    background_file: str = Field(default="", max_length=200)
    background_url: str = Field(default="", max_length=600)
    background_start: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    background_color: str = Field(default="0x12161C", pattern=r"^0x[0-9A-Fa-f]{6}$")
    # `None` = padrão do modo: link mantém o som, pasta vem muda (loop com som dá tranco).
    background_audio: bool | None = None
    # Revelação: som tocado quando a resposta aparece e animação que acompanha.
    reveal_sound: Literal["none", "acerto", "ding", "tada", "pop", "custom"] = "acerto"
    reveal_sound_file: str = Field(default="", pattern=r"^$|" + MUSIC_NAME)
    reveal_sound_volume: float = Field(default=0.8, ge=0.1, le=1, allow_inf_nan=False)
    reveal_effect: Literal["none", "pulse", "confetti"] = "confetti"

    @field_validator("headline")
    @classmethod
    def tidy(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Informe a frase do topo")
        return value

    @field_validator("background_url")
    @classmethod
    def supported(cls, value):
        if value and not (is_tiktok_url(value) or is_supported_url(value)):
            raise ValueError("Fundo: use um link do TikTok, YouTube, Twitch ou Kick (ou deixe vazio)")
        return value

    @field_validator("background_file")
    @classmethod
    def plain_name(cls, value):
        if value and ("/" in value or "\\" in value or value.startswith(".")):
            raise ValueError("Fundo: escolha um vídeo da pasta de fundos")
        return value

    @model_validator(mode="after")
    def background_mode(self):
        if self.background is None:
            self.background = "url" if self.background_url else "color"
        if self.background == "url" and not self.background_url:
            raise ValueError("Fundo: cole o link do vídeo ou escolha outro tipo de fundo")
        if self.background_audio is None:
            self.background_audio = self.background == "url"
        return self

    @model_validator(mode="after")
    def custom_sound(self):
        if self.reveal_sound == "custom" and not self.reveal_sound_file:
            raise ValueError("Som da revelação: envie o arquivo do seu som ou escolha um dos prontos")
        return self

    @model_validator(mode="after")
    def choices_complete(self):
        if self.style != "choices":
            return self
        for number, item in enumerate(self.questions, 1):
            options = [item.answer, *item.wrong]
            if len(item.wrong) != 3 or not all(item.wrong):
                raise ValueError(f"Pergunta {number}: preencha as três alternativas erradas")
            if any(len(option) > CHOICE_MAX for option in options):
                raise ValueError(f"Pergunta {number}: cada alternativa deve ter até {CHOICE_MAX} caracteres")
            if len({option.casefold() for option in options}) != 4:
                raise ValueError(f"Pergunta {number}: as quatro alternativas precisam ser diferentes")
        return self

    @property
    def block(self) -> float:
        return self.suspense + self.reveal

    @property
    def total(self) -> float:
        return self.block * len(self.questions)


def arrange(spec: Quiz) -> list[tuple[list[str], int]]:
    """Ordem das alternativas e posição da certa, pergunta a pergunta.

    A semente vem do conteúdo: renderizar de novo o mesmo quiz dá as mesmas letras,
    e o gabarito da descrição continua valendo. As posições certas são distribuídas
    entre A, B, C e D em vez de sorteadas uma a uma, que às vezes dá "C" três vezes.
    """
    payload = json.dumps([[q.question, q.answer, q.wrong] for q in spec.questions], ensure_ascii=False)
    rng = random.Random(hashlib.sha256(payload.encode()).hexdigest())
    slots = [i % 4 for i in range(len(spec.questions))]
    rng.shuffle(slots)
    arranged = []
    for item, slot in zip(spec.questions, slots):
        wrong = list(item.wrong)
        rng.shuffle(wrong)
        arranged.append((wrong[:slot] + [item.answer] + wrong[slot:], slot))
    return arranged


def _wrapped(text: str, width: int) -> str:
    return "\\N".join(text_literal(line) for line in textwrap.wrap(text, width=width) or [""])


_CARD_SHAPE = f"m 0 0 l {_CARD_W} 0 l {_CARD_W} {_CARD_H} l 0 {_CARD_H}"
# Coordenadas positivas: o libass alinha o desenho a partir de (0,0), e um círculo
# centrado na origem sairia deslocado meio diâmetro para a esquerda.
_CIRCLE = "m 100 0 b 155 0 200 45 200 100 b 200 155 155 200 100 200 b 45 200 0 155 0 100 b 0 45 45 0 100 0"
CONFETTI_PIECES = 26
_CONFETTI =["&H45DDFF&", "&H80FF80&", "&HB56FFF&", "&HFFC85E&", "&HFFFFFF&"]


def _card(center_y: int, colour: str, alpha: str, extra: str = "") -> str:
    # Desenho vetorial do ASS: o cartão tem tempo próprio, então pode trocar de cor
    # na revelação sem um segundo filtro de vídeo. Ancorado no centro (\\an5) para
    # que a animação de escala cresça para os lados, e não a partir do canto.
    return (f"{{\\an5\\pos({PLAY_W // 2},{center_y})\\p1\\bord0\\shad0\\1c{colour}\\1a{alpha}{extra}}}"
            f"{_CARD_SHAPE}{{\\p0}}")


def reveal_fx(spec: Quiz, index: int, at: float, center_y: int, shape: str) -> list[tuple]:
    """Eventos da animação de revelação: clarão, onda e, se pedido, confete.

    Tudo em ASS com `\\t` e `\\move`: o libass anima, sem quadro extra nem
    filtro de vídeo. O confete usa semente fixa por pergunta, então renderizar de
    novo dá exatamente a mesma animação.
    """
    if spec.reveal_effect == "none":
        return []
    events = [
        # Clarão curto na tela inteira: marca o instante da virada.
        (3, at, at + .2, f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c&HFFFFFF&\\1a&HB8&\\t(0,180,\\1a&HFF&)}}"
                         f"m 0 0 l {PLAY_W} 0 l {PLAY_W} {PLAY_H} l 0 {PLAY_H}{{\\p0}}"),
        # Onda que sai da resposta e some.
        (3, at, at + .6, f"{{\\an5\\pos({PLAY_W // 2},{center_y})\\p1\\bord7\\shad0\\1a&HFF&\\3c&H80FF80&\\3a&H10&"
                         f"\\fscx60\\fscy60\\t(0,550,\\fscx{'125' if shape == 'card' else '380'}\\fscy{'190' if shape == 'card' else '150'}\\3a&HFF&)}}"
                         f"{_CARD_SHAPE if shape == 'card' else _CIRCLE}{{\\p0}}"),
    ]
    if spec.reveal_effect == "confetti":
        rng = random.Random(f"confete-{index}")
        for piece in range(CONFETTI_PIECES):
            angle = math.radians(rng.uniform(200, 340) if piece % 3 else rng.uniform(0, 360))
            distance = rng.uniform(260, 560)
            x2 = round(PLAY_W // 2 + math.cos(angle) * distance)
            # Um pouco de "gravidade": o ponto final cai abaixo da trajetória reta.
            y2 = round(center_y + math.sin(angle) * distance + rng.uniform(80, 220))
            spin = rng.randint(0, 359)
            life = rng.uniform(.75, 1.05)
            w, h = rng.choice([(30, 16), (22, 22), (36, 11)])
            events.append((4, at, at + life,
                           f"{{\\an5\\move({PLAY_W // 2},{center_y},{x2},{y2},0,{round(life * 1000)})\\p1\\bord0\\shad0"
                           f"\\1c{rng.choice(_CONFETTI)}\\frz{spin}\\t(0,{round(life * 1000)},\\frz{spin + rng.choice([-1, 1]) * 540})"
                           f"\\fad(0,{round(life * 400)})}}m 0 0 l {w} 0 l {w} {h} l 0 {h}{{\\p0}}"))
    return events


def quiz_ass(spec: Quiz, path: Path) -> Path:
    """Roteiro do quiz: pergunta, contador e resposta, bloco a bloco."""
    styles = [
        "Style: Head,Arial,52,&H00B9C4D0,&H00B9C4D0,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,3,1,8,50,50,0,1",
        "Style: Ask,Arial,76,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,5,2,8,60,60,0,1",
        "Style: Tick,Arial,190,&H0045DDFF,&H0045DDFF,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,6,3,5,0,0,0,1",
        "Style: Answer,Arial,88,&H0080FF80,&H0080FF80,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,5,2,8,60,60,0,1",
        "Style: Step,Arial,44,&H0090A0B0,&H0090A0B0,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,3,1,2,50,50,0,1",
        "Style: Card,Arial,50,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,"
        "100,100,0,0,1,3,1,4,0,0,0,1",
    ]
    total = spec.total
    choices = spec.style == "choices"
    events = [f"Dialogue: 0,0:00:00.00,{_ts(total)},Head,,0,0,0,,"
              f"{{\\pos({PLAY_W // 2},{_Y_HEADLINE})\\an8}}{text_literal(spec.headline)}\n"]

    def stamp(value: float) -> str:
        # Truncar para centésimos mantém o texto alinhado ao quadro; arredondar
        # para cima faria um bloco invadir o começo do seguinte.
        return _ts(math.floor(min(value, total) * 100 + 1e-6) / 100)

    def line(layer: int, start: float, end: float, style: str, text: str) -> None:
        events.append(f"Dialogue: {layer},{stamp(start)},{stamp(end)},{style},,0,0,0,,{text}\n")

    layout = arrange(spec) if choices else [None] * len(spec.questions)
    for index, (item, arranged) in enumerate(zip(spec.questions, layout)):
        opens = index * spec.block
        reveals = opens + spec.suspense
        closes = opens + spec.block
        ask_y = _Y_CHOICE_QUESTION if choices else _Y_QUESTION
        line(1, opens, closes, "Ask", f"{{\\pos({PLAY_W // 2},{ask_y})\\an8}}{_wrapped(item.question, 22 if choices else 20)}")
        line(1, opens, closes, "Step", f"{{\\pos({PLAY_W // 2},{_Y_PROGRESS})\\an5}}{index + 1} de {len(spec.questions)}")
        if spec.countdown:
            # Um número por segundo inteiro de suspense; a sobra vira o primeiro,
            # que fica um pouco mais tempo em vez de aparecer um número cortado.
            ticks = max(1, int(spec.suspense))
            first = opens + (spec.suspense - ticks)
            tick_y = _Y_CHOICE_COUNTDOWN if choices else _Y_COUNTDOWN
            size = "\\fs150" if choices else ""
            for n in range(ticks, 0, -1):
                begin = first + (ticks - n)
                # O primeiro número já nasce com a pergunta e absorve a fração;
                # sem isso sobraria um trecho sem número antes da contagem.
                line(2, opens if n == ticks else begin, begin + 1, "Tick", f"{{\\pos({PLAY_W // 2},{tick_y})\\an5{size}\\fad(120,120)}}{n}")
        animated = spec.reveal_effect != "none"
        if not choices:
            # A resposta nasce pequena, passa do tamanho e assenta: um "pop".
            pop = "\\fscx30\\fscy30\\t(0,170,\\fscx118\\fscy118)\\t(170,320,\\fscx100\\fscy100)" if animated else ""
            line(2, reveals, closes, "Answer",
                 f"{{\\pos({PLAY_W // 2},{_Y_ANSWER})\\an8\\fad({90 if animated else 160},0){pop}}}{_wrapped(item.answer, 18)}")
            for layer, start, end, text in reveal_fx(spec, index, reveals, _Y_ANSWER + 55, "circle"):
                line(layer, start, min(end, closes), "Card", text)
            continue
        options, correct = arranged
        for slot, option in enumerate(options):
            middle = _CARD_TOP + slot * _CARD_STEP + _CARD_H // 2
            label = f"{LETTERS[slot]})  {_wrapped(option, 26)}"
            text_at = f"{{\\pos({_CARD_X + 50},{middle})\\an4}}"
            line(1, opens, reveals, "Card", _card(middle, "&H00302824&", "&H30&"))
            line(2, opens, reveals, "Card", text_at + label)
            if slot == correct:
                pop = "\\fscx94\\fscy94\\t(0,150,\\fscx105\\fscy112)\\t(150,300,\\fscx100\\fscy100)" if animated else ""
                line(1, reveals, closes, "Card", _card(middle, "&H0040B040&", "&H10&", pop))
                line(2, reveals, closes, "Card", f"{{\\pos({_CARD_X + 50},{middle})\\an4\\fscx104\\fscy104}}{label}")
                for layer, start, end, text in reveal_fx(spec, index, reveals, middle, "card"):
                    line(layer, start, min(end, closes), "Card", text)
            else:
                # As erradas apagam aos poucos em vez de trocar de uma vez.
                fade_card = "\\1a&H30&\\t(0,260,\\1a&H90&)" if animated else ""
                fade_text = "\\t(0,260,\\1c&H00707070&)" if animated else "\\1c&H00707070&"
                line(1, reveals, closes, "Card", _card(middle, "&H00302824&", "&H90&", fade_card))
                line(2, reveals, closes, "Card", f"{{\\pos({_CARD_X + 50},{middle})\\an4{fade_text}}}{label}")
    path.write_text(ass_document(styles, events), encoding="utf-8")
    return path


def reveal_times(spec: Quiz) -> list[float]:
    return [index * spec.block + spec.suspense for index in range(len(spec.questions))]


def answer_key(spec: Quiz) -> str:
    if spec.style != "choices":
        return "\n".join(f"{i + 1}. {q.question} → {q.answer}" for i, q in enumerate(spec.questions))
    return "\n".join(f"{i + 1}. {q.question} → {LETTERS[correct]}) {q.answer}"
                     for i, (q, (_, correct)) in enumerate(zip(spec.questions, arrange(spec))))


def _background(spec: Quiz, source: Path | None, duration: float, width: int, height: int,
                work: Path, start: float | None = None) -> tuple[str, bool]:
    """Prepara o fundo e diz se ele traz áudio. Devolve o nome do arquivo."""
    if source is None:
        ffmpeg(["-f", "lavfi", "-i", f"color=c={spec.background_color}:s={width}x{height}:r=30:d={duration:.3f}",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v", "-map", "1:a", "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "background.mov"], work)
        return "background.mov", False
    info = probe(source)
    start = spec.background_start if start is None else start
    if info.duration - start < 1.0:
        raise ValueError("Fundo: o início escolhido deixa menos de 1 segundo de vídeo")
    # Fundo mais curto que o quiz recomeça em loop em vez de congelar no fim.
    audio = info.has_audio and bool(spec.background_audio)
    normalize(source, "background.mov", width, height, start, duration, work,
              cover=True, has_audio=audio, loop=True)
    return "background.mov", audio


def render_quiz(spec: Quiz, background: Path | None, directory: Path,
                progress=lambda *_: None, width: int = PLAY_W, height: int = PLAY_H,
                background_start: float | None = None) -> dict:
    directory = directory.resolve()
    work = directory / "quiz-work"
    clips = directory / "clips"
    work.mkdir(parents=True, exist_ok=True)
    clips.mkdir(parents=True, exist_ok=True)
    duration = round(spec.total, 3)
    music = music_path(spec.music) if spec.music else None

    progress("preparando o fundo", .40)
    name, has_audio = _background(spec, background, duration, width, height, work, background_start)
    progress("escrevendo perguntas e respostas", .70)
    quiz_ass(spec, work / "quiz.ass")
    # Faixas atrás do texto: sem elas a letra some em fundo claro. No estilo com
    # alternativas os cartões já fazem esse papel; só a pergunta precisa de faixa.
    bands = ("drawbox=x=0:y=ih*.17:w=iw:h=ih*.17:color=black@0.45:t=fill," if spec.style == "choices" else
             "drawbox=x=0:y=ih*.26:w=iw:h=ih*.15:color=black@0.45:t=fill,"
             "drawbox=x=0:y=ih*.66:w=iw:h=ih*.13:color=black@0.45:t=fill,")
    temporary = clips / "quiz-rendering.mp4"
    ffmpeg(["-i", name, "-vf", bands + "ass=quiz.ass", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(temporary)], work)
    if music:
        progress("mixando a música", .88)
        mixed = clips / "quiz-music.mp4"
        add_music(temporary, music, mixed, duration, spec, work)
        mixed.replace(temporary)
    if spec.reveal_sound != "none":
        # Depois da música: assim o som da revelação não é abafado pelo ducking.
        progress("adicionando o som da revelação", .92)
        sound = (music_path(spec.reveal_sound_file) if spec.reveal_sound == "custom"
                 else sounds.preset_path(spec.reveal_sound))
        cued = clips / "quiz-cues.mp4"
        sounds.add_cues(temporary, sound, cued, duration, reveal_times(spec), spec.reveal_sound_volume, work)
        cued.replace(temporary)

    actual = probe(temporary)
    if abs(actual.duration - duration) > .15:
        raise RuntimeError("A montagem não passou na verificação de duração")
    final = clips / "quiz.mp4"
    temporary.replace(final)
    ffmpeg(["-i", str(final), "-ss", f"{spec.suspense * 0.5:.2f}", "-frames:v", "1", "-update", "1",
            str(clips / "quiz-cover.jpg")], work)

    gabarito = answer_key(spec)
    warnings = []
    if not has_audio and not music:
        warnings.append(("Só o som da revelação toca: " if spec.reveal_sound != "none" else "Vídeo sem áudio: ")
                        + "envie uma música ou escolha uma no editor do YouTube ou do TikTok antes de publicar.")
    arranged = arrange(spec) if spec.style == "choices" else None
    questions = []
    for i, q in enumerate(spec.questions):
        entry = {"question": q.question, "answer": q.answer}
        if arranged:
            entry.update(options=arranged[i][0], correct=LETTERS[arranged[i][1]])
        questions.append(entry)
    return {"kind": "quiz", "source_duration": duration, "source_resolution": f"{width}×{height}",
            "provider": "montagem de quiz", "headline": spec.headline, "style": spec.style,
            "background": {"mode": spec.background,
                           "file": background.name if background is not None and spec.background == "folder" else "",
                           "start": background_start if background_start is not None else spec.background_start},
            "music": bool(music), "questions": questions,
            "reveal": {"sound": spec.reveal_sound, "effect": spec.reveal_effect},
            "timing": {"suspense": spec.suspense, "reveal": spec.reveal, "block": spec.block},
            "clips": [{"index": 1, "revision": 0, "file": "quiz.mp4", "thumbnail": "quiz-cover.jpg",
                       "title": spec.headline, "text": gabarito, "description": gabarito,
                       "actual_duration": actual.duration, "source_start": 0, "source_end": duration,
                       "provider": "montagem de quiz", "warnings": warnings}]}


def _folder_background(spec: Quiz, settings: dict, recent, save_settings) -> tuple[Path, float, dict]:
    """Resolve o fundo da pasta e grava a escolha no projeto.

    Gravar antes de renderizar faz uma nova tentativa usar o mesmo vídeo e o mesmo
    começo, e deixa o próximo quiz saber qual fundo acabou de ser usado.
    """
    pick = settings.get("background_pick") or {}
    # Reaproveita a escolha anterior deste projeto se ela ainda vale: modo automático,
    # ou o mesmo vídeo que a pessoa fixou. Se o arquivo sumiu da pasta, escolhe de novo.
    if pick.get("file") and spec.background_file in ("", pick["file"]):
        try:
            return backgrounds.background_path(pick["file"]), float(pick.get("start", 0)), settings
        except ValueError:
            pass
    if spec.background_file:
        path = backgrounds.background_path(spec.background_file)
        duration = probe(path).duration
    else:
        chosen = backgrounds.pick_background(recent())
        path, duration = backgrounds.background_path(chosen["name"]), chosen["duration"]
    start = backgrounds.random_start(duration, spec.total)
    settings = {**settings, "background_pick": {"file": path.name, "start": start}}
    save_settings(settings)
    return path, start, settings


def process_quiz(settings: dict, directory: Path, progress=lambda *_: None, *,
                 avoid: Callable[[], list[str]] = lambda: [],
                 save_settings: Callable[[dict], None] = lambda _: None,
                 recent_backgrounds: Callable[[], list[str]] = lambda: []) -> dict:
    """Monta o quiz. Com `settings["generate"]`, pede as perguntas à IA antes.

    As perguntas geradas são gravadas de volta nas configurações do projeto antes de
    renderizar: uma nova tentativa depois de falha no ffmpeg não paga a IA outra vez
    nem troca as perguntas, e o lote consulta o que já saiu para não repetir.
    """
    started = time.time()
    generation = settings.get("generate")
    generated = None
    if generation and not settings["quiz"].get("questions"):
        from .quiz_ai import generate_questions
        progress("gerando perguntas com IA", .03)
        generated = generate_questions(
            generation["topic"], int(generation["count"]), style=settings["quiz"].get("style", "open"),
            difficulty=generation.get("difficulty", "medio"), avoid=avoid())
        settings = {**settings, "quiz": {**settings["quiz"], "questions": generated["questions"]},
                    "generate": {**generation, "provider": generated["provider"], "model": generated["model"],
                                 "warnings": generated["warnings"]}}
        save_settings(settings)
    spec = Quiz.model_validate(settings["quiz"])
    background, start = None, None
    if spec.background == "folder":
        background, start, settings = _folder_background(spec, settings, recent_backgrounds, save_settings)
    elif spec.background == "url":
        progress("baixando o vídeo de fundo", .10)
        folder = directory / "source" / "background"
        marker = folder / "download.json"
        cached = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        path = folder / cached.get("file", "missing")
        if cached.get("url") != spec.background_url or not path.is_file() \
                or not path.resolve().is_relative_to(folder.resolve()):
            try:
                path = fetch_source(spec.background_url, folder)
            except Exception as exc:
                raise DownloadError(f"Vídeo de fundo: {exc}") from exc
            atomic_json(marker, {"url": spec.background_url, "file": path.name})
        background = path
    manifest = render_quiz(spec, background, directory, progress, background_start=start)
    generation = settings.get("generate")
    if generation:
        manifest["generated"] = {k: generation.get(k) for k in ("topic", "difficulty", "provider", "model")}
        manifest["clips"][0]["warnings"] = [
            "Perguntas geradas por IA: confira o gabarito antes de publicar.",
            *generation.get("warnings", []), *manifest["clips"][0]["warnings"]]
    if settings.get("batch"):
        manifest["batch"] = settings["batch"]
    manifest["elapsed_seconds"] = round(time.time() - started, 2)
    return manifest
