"""Pasta de fundos do quiz: vídeos que a pessoa solta numa pasta e o app usa sozinho.

Nada de cadastro: o que estiver na pasta é o catálogo. Cada quiz em modo automático
pega o vídeo usado há mais tempo (ou nunca usado), então um lote de dez quizzes não
sai com o mesmo fundo três vezes seguidas. O ponto de partida também é sorteado
quando o vídeo é mais longo que o quiz, para o mesmo fundo não abrir sempre igual.
"""
from __future__ import annotations

import hashlib
import random
import threading
from pathlib import Path

from .config import STORAGE
from .montage import ffmpeg
from .probe import ProbeError, probe

BACKGROUND_DIR = STORAGE / "fundos-quiz"
THUMB_DIR = STORAGE / "cache" / "fundos-quiz"
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
README = """Coloque aqui os vídeos de fundo dos quizzes (.mp4, .mov, .webm, .mkv ou .m4v).

O CutClips usa estes vídeos sozinho: no modo automático cada quiz pega o fundo
usado há mais tempo e repete o vídeo em loop enquanto o quiz durar.
Vídeos verticais (9:16) ficam melhores; horizontais são recortados no centro.
"""

_cache: dict[tuple, dict] = {}
_lock = threading.Lock()


def ensure_folder() -> Path:
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    readme = BACKGROUND_DIR / "LEIA-ME.txt"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")
    return BACKGROUND_DIR


def background_path(name: str) -> Path:
    """Caminho de um vídeo da pasta. Recusa qualquer coisa fora dela."""
    folder = BACKGROUND_DIR.resolve()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Fundo inválido")
    path = (folder / name).resolve()
    if path.parent != folder or path.suffix.lower() not in VIDEO_EXT or not path.is_file():
        raise ValueError(f"O vídeo de fundo “{name}” não está mais na pasta de fundos")
    return path


def list_backgrounds() -> list[dict]:
    """Vídeos da pasta com duração e tamanho. Arquivo ilegível vem marcado, não some."""
    ensure_folder()
    videos = []
    for path in sorted(BACKGROUND_DIR.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXT or path.name.startswith("."):
            continue
        stat = path.stat()
        key = (path.name, stat.st_size, stat.st_mtime_ns)
        with _lock:
            info = _cache.get(key)
        if info is None:
            try:
                media = probe(path)
                info = {"duration": round(media.duration, 2), "width": media.width,
                        "height": media.height, "has_audio": media.has_audio, "error": ""}
            except (ProbeError, ValueError, OSError) as exc:
                info = {"duration": 0, "width": 0, "height": 0, "has_audio": False,
                        "error": f"Não foi possível ler este vídeo ({type(exc).__name__})"}
            with _lock:
                _cache[key] = info
        videos.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime, **info})
    return videos


def usable() -> list[dict]:
    return [video for video in list_backgrounds() if not video["error"] and video["duration"] >= 1]


def pick_background(recent: list[str], rng: random.Random | None = None) -> dict:
    """Escolhe o fundo usado há mais tempo. `recent` vem do mais novo para o mais antigo."""
    videos = usable()
    if not videos:
        raise ValueError(f"A pasta de fundos está vazia. Coloque vídeos em {BACKGROUND_DIR}")
    rng = rng or random.Random()
    last_use = {}
    for position, name in enumerate(recent):
        last_use.setdefault(name, position)
    never = [video for video in videos if video["name"] not in last_use]
    if never:
        return rng.choice(never)
    oldest = max(last_use[video["name"]] for video in videos)
    return rng.choice([video for video in videos if last_use[video["name"]] == oldest])


def random_start(duration: float, needed: float, rng: random.Random | None = None) -> float:
    """Começo sorteado quando sobra vídeo; senão do início, e o loop cobre o resto."""
    spare = duration - needed
    if spare <= 1:
        return 0.0
    return round((rng or random).uniform(0, spare), 1)


def thumbnail(name: str) -> Path:
    """Miniatura vertical em cache, refeita quando o arquivo muda."""
    source = background_path(name)
    stat = source.stat()
    # Absoluto pelo mesmo motivo dos sons: o ffmpeg roda dentro da pasta de cache.
    folder = THUMB_DIR.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    # hashlib, não hash(): o hash de texto muda a cada processo e perderia o cache.
    key = hashlib.sha1(f"{name}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:20]
    target = folder / f"{key}.jpg"
    if not target.is_file():
        try:
            at = min(1.0, max(0.0, probe(source).duration * 0.1))
        except ProbeError:
            at = 0.0
        temporary = target.with_suffix(".tmp.jpg")
        ffmpeg(["-ss", f"{at:.2f}", "-i", str(source), "-frames:v", "1", "-vf",
                "scale=270:480:force_original_aspect_ratio=increase,crop=270:480", "-update", "1",
                str(temporary)], folder, timeout=60)
        temporary.replace(target)
    return target
