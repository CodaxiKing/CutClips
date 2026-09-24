import os
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from api import db
from cutclips import backgrounds, montage, narrate, quiz_ai, quiz_bank, sounds
from cutclips.quiz import Quiz
from cutclips.narrate import Narration
from cutclips.reaction import Reaction
from cutclips.select import ProviderError

router = APIRouter(tags=["Montagens"])
MAX_MUSIC_BYTES = 50 * 1024**2


@router.post("/api/quiz", status_code=202)
def create_quiz(data: Quiz):
    _check_music(data.music)
    _check_music(data.reveal_sound_file)
    _check_background(data)
    job_id = db.create_job("quiz", title=data.headline, settings={"kind": "quiz", "quiz": data.model_dump()})
    return {"job_id": job_id, "status": "queued"}


@router.get("/api/quiz/themes")
def quiz_themes():
    return {"themes": quiz_bank.themes()}


@router.get("/api/quiz/suggestions")
def quiz_suggestions(theme: str = Query("", max_length=40), count: int = Query(5, ge=1, le=10),
                     exclude: list[str] = Query(default=[], max_length=20)):
    try:
        return {"questions": quiz_bank.suggest(theme, count, set(exclude))}
    except KeyError:
        raise HTTPException(404, "tema não encontrado")


@router.get("/api/quiz/sounds")
def quiz_sounds():
    return {"sounds": [{"id": key, "label": label} for key, (label, _, _) in sounds.PRESETS.items()]}


@router.get("/api/quiz/sounds/{name}")
async def quiz_sound(name: str):
    try:
        path = await run_in_threadpool(sounds.preset_path, name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})


# --------------------------------------------------------------------------- #
# Pasta de fundos
# --------------------------------------------------------------------------- #

def _check_background(spec: Quiz) -> None:
    """Recusa na hora um quiz de pasta que não teria fundo, em vez de falhar no worker."""
    if spec.background != "folder":
        return
    try:
        if spec.background_file:
            backgrounds.background_path(spec.background_file)
        elif not backgrounds.usable():
            raise ValueError(f"A pasta de fundos está vazia. Coloque vídeos em {backgrounds.BACKGROUND_DIR}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/quiz/backgrounds")
async def quiz_backgrounds():
    videos = await run_in_threadpool(backgrounds.list_backgrounds)
    return {"folder": str(backgrounds.BACKGROUND_DIR.resolve()), "videos": videos,
            "can_open": hasattr(os, "startfile")}


@router.get("/api/quiz/backgrounds/{name}/thumbnail")
async def quiz_background_thumbnail(name: str):
    try:
        path = await run_in_threadpool(backgrounds.thumbnail, name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(422, "Não foi possível gerar a miniatura deste vídeo") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.post("/api/quiz/backgrounds/open")
def open_backgrounds_folder():
    folder = backgrounds.ensure_folder()
    # Só existe no Windows, e só faz sentido com a API rodando na mesma máquina de
    # quem clicou — que é o caso do CutClips local. Em Docker a tela mostra o caminho.
    if not hasattr(os, "startfile"):
        raise HTTPException(501, f"Abra a pasta manualmente: {folder.resolve()}")
    os.startfile(folder.resolve())  # noqa: S606
    return {"folder": str(folder.resolve())}


# --------------------------------------------------------------------------- #
# IA
# --------------------------------------------------------------------------- #

Difficulty = Literal["facil", "medio", "dificil"]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    topic: str = Field(min_length=2, max_length=120)
    count: int = Field(default=5, ge=3, le=10)
    style: Literal["open", "choices"] = "open"
    difficulty: Difficulty = "medio"
    avoid: list[str] = Field(default_factory=list, max_length=60)


@router.get("/api/quiz/ai")
def quiz_ai_status():
    return quiz_ai.ai_status()


@router.post("/api/quiz/generate")
async def quiz_generate(data: GenerateRequest):
    try:
        return await run_in_threadpool(quiz_ai.generate_questions, data.topic, data.count, style=data.style,
                                       difficulty=data.difficulty, avoid=data.avoid)
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------------------------------------- #
# Lote
# --------------------------------------------------------------------------- #

class QuizBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # Ritmo, estilo, fundo e música: tudo do quiz menos frase e perguntas.
    base: dict = Field(default_factory=dict)
    headline: str = Field(min_length=1, max_length=54)
    videos: int = Field(ge=2, le=14)
    per_video: int = Field(default=5, ge=3, le=10)
    source: Literal["bank", "ai"] = "bank"
    theme: str = Field(default="", max_length=40)
    topic: str = Field(default="", max_length=120)
    difficulty: Difficulty = "medio"
    start_date: date
    every_days: int = Field(default=1, ge=1, le=7)

    @field_validator("headline")
    @classmethod
    def tidy(cls, value):
        return " ".join(value.split())


def _headline(template: str, number: int) -> str:
    return template.replace("{n}", str(number)) if "{n}" in template else f"{template} #{number}"


def _placeholder(count: int) -> list[dict]:
    return [{"question": f"Pergunta {i + 1}?", "answer": "A", "wrong": ["B", "C", "D"]} for i in range(count)]


@router.post("/api/quiz/batch", status_code=202)
def create_quiz_batch(data: QuizBatch):
    base = {k: v for k, v in data.base.items() if k not in {"headline", "questions"}}
    try:
        # Valida o que é comum a todos antes de criar qualquer projeto: um lote pela
        # metade na fila é pior do que um erro claro.
        sample = Quiz.model_validate({**base, "headline": _headline(data.headline, data.videos),
                                      "questions": _placeholder(data.per_video)})
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())) from exc
    _check_music(base.get("music", ""))
    _check_music(sample.reveal_sound_file)
    _check_background(sample)

    if data.source == "bank":
        try:
            groups = quiz_bank.draw_batch(data.theme, data.videos, data.per_video)
        except KeyError:
            raise HTTPException(404, "tema não encontrado")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        label = next((t["label"] for t in quiz_bank.themes() if t["id"] == data.theme), "Todos os temas")
    else:
        if len(data.topic) < 2:
            raise HTTPException(422, "Informe o tema para a IA")
        status = quiz_ai.ai_status()
        if not status["ready"]:
            raise HTTPException(503, status["message"])
        groups = [[] for _ in range(data.videos)]
        label = data.topic

    batch_id = uuid.uuid4().hex[:10]
    jobs = []
    for index, questions in enumerate(groups):
        headline = _headline(data.headline, index + 1)
        settings = {"kind": "quiz", "quiz": {**base, "headline": headline, "questions": questions},
                    "batch": {"id": batch_id, "index": index + 1, "total": data.videos, "label": label,
                              "source": data.source,
                              "publish_on": (data.start_date + timedelta(days=index * data.every_days)).isoformat()}}
        if data.source == "ai":
            settings["generate"] = {"topic": data.topic, "difficulty": data.difficulty, "count": data.per_video}
        jobs.append(db.create_job("quiz", title=headline, settings=settings))
    return {"batch_id": batch_id, "jobs": jobs, "status": "queued"}


@router.get("/api/quiz/batches")
def quiz_batches():
    batches: dict[str, dict] = {}
    for job in db.batch_jobs():
        info = job["settings"]["batch"]
        batch = batches.setdefault(info["id"], {"id": info["id"], "label": info.get("label", ""),
                                                "source": info.get("source", "bank"),
                                                "created_at": job["created_at"], "items": []})
        batch["created_at"] = min(batch["created_at"], job["created_at"])
        batch["items"].append({"job_id": job["id"], "title": job["title"], "status": job["status"],
                               "stage": job["stage"], "progress": job["progress"], "error": job["error"],
                               "index": info.get("index"), "publish_on": info.get("publish_on")})
    ordered = sorted(batches.values(), key=lambda b: b["created_at"], reverse=True)
    for batch in ordered:
        batch["items"].sort(key=lambda item: item["index"] or 0)
    return {"batches": ordered}


# --------------------------------------------------------------------------- #
# Reação e música
# --------------------------------------------------------------------------- #

@router.post("/api/reaction", status_code=202)
def create_reaction(data: Reaction):
    _check_music(data.music)
    fallback = ("Antes", "Depois") if data.layout == "side" else ("Original", "Reação")
    title = data.headline or f"{data.top.label or fallback[0]} + {data.bottom.label or fallback[1]}"
    job_id = db.create_job("reaction", title=title, settings={"kind": "reaction", "reaction": data.model_dump()})
    return {"job_id": job_id, "status": "queued"}


@router.post("/api/narration", status_code=202)
def create_narration(data: Narration):
    _check_music(data.music)
    if not data.script and not os.getenv("ANTHROPIC_API_KEY", "").strip()             and not os.getenv("OPENAI_API_KEY", "").strip():
        # Sem IA e sem roteiro não há o que narrar; dizer isso agora poupa um job
        # que só falharia no worker vários segundos depois.
        raise HTTPException(422, "Escreva o roteiro ou configure a chave da IA no .env "
                                 "para que ela escreva por você.")
    if not data.voice:
        # Voz não escolhida = voz da influencer. É o que faz o perfil valer para
        # qualquer vídeo narrado sem a interface precisar lembrar de mandar ela.
        profile = narrate.influencer_voice()
        if profile["voice"]:
            data = data.model_copy(update={"voice": profile["voice"], "voice_rate": profile["rate"]})
    job_id = db.create_job("narration", title=data.subject,
                           settings={"kind": "narration", "narration": data.model_dump()})
    return {"job_id": job_id, "status": "queued"}


@router.get("/api/narration/voices")
def narration_voices() -> dict:
    """Vozes do sistema, para a interface oferecer as instaladas de verdade."""
    voices = narrate.list_voices()
    status = narrate.piper_status()
    return {"voices": voices,
            "default": next((v for v in voices if v.startswith(narrate.PIPER_PREFIX)),
                            next((v for v in voices if "Maria" in v or "Daniel" in v),
                                 voices[0] if voices else "")),
            "influencer": narrate.influencer_voice(),
            "piper": {"ready": status["ready"], "missing": status["missing"]}}


class VoiceProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    voice: str = Field(default="", max_length=80)
    rate: int = Field(default=0, ge=-10, le=10)


@router.get("/api/narration/voice")
def get_influencer_voice() -> dict:
    """Perfil da voz da influencer: a que a narração e o lip-sync usam por padrão."""
    return narrate.influencer_voice()


@router.post("/api/narration/voice", status_code=201)
def set_influencer_voice(data: VoiceProfile) -> dict:
    voices = narrate.list_voices()
    if data.voice and voices and data.voice not in voices:
        raise HTTPException(422, "Essa voz não está instalada nesta máquina. Escolha uma das vozes listadas.")
    return narrate.save_influencer_voice(data.voice, data.rate)


def _check_music(name: str) -> None:
    if name:
        try:
            montage.music_path(name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post("/api/music", status_code=201)
async def upload_music(file: UploadFile = File(...), kind: Literal["music", "sound"] = Query("music")):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in montage.MUSIC_EXT:
        raise HTTPException(400, "Formatos aceitos: " + ", ".join(sorted(e[1:] for e in montage.MUSIC_EXT)))
    montage.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + suffix
    target = montage.MUSIC_DIR / name
    size = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_MUSIC_BYTES:
                    raise HTTPException(413, "A música deve ter até 50 MB")
                out.write(chunk)
        # Efeito sonoro pode ser curtinho; música precisa de pelo menos 1 segundo.
        limits = {"minimum": 0.1, "maximum": 30} if kind == "sound" else {}
        duration = await run_in_threadpool(lambda: montage.audio_duration(target, **limits))
    except ValueError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {"music": name, "filename": Path(file.filename or name).name[:120], "duration": round(duration, 2)}
