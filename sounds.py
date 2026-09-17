"""Sons curtos de revelação, sintetizados pelo próprio ffmpeg.

Gerar em vez de embutir arquivos evita qualquer dúvida de licença: cada som é uma
fórmula de senos com envelope de decaimento, então o resultado é nosso. O arquivo
fica em cache e só é refeito se a fórmula mudar.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import STORAGE
from .montage import ffmpeg

SOUND_DIR = STORAGE / "cache" / "sons"


def _note(freq: float, at: float, decay: float, gain: float) -> str:
    # Ataque de 4 ms (sem ele cada nota começa com um estalo) e decaimento exponencial.
    return (f"{gain}*gte(t,{at})*min(1,(t-{at})*250)"
            f"*sin(2*PI*{freq}*(t-{at}))*exp(-{decay}*(t-{at}))")


def _chord(notes: list[tuple[float, float, float, float]]) -> str:
    return "+".join(_note(*note) for note in notes)


# nome: (rótulo, expressão do aevalsrc, duração em segundos)
PRESETS: dict[str, tuple[str, str, float]] = {
    "acerto": ("Acerto", _chord([(1046.5, 0, 6, .45), (2093, 0, 9, .12),
                                 (1568, .11, 4.5, .5), (3136, .11, 8, .12)]), 1.2),
    "ding": ("Ding", _chord([(1318.5, 0, 3.5, .55), (2637, 0, 6, .18), (3955.5, 0, 9, .06)]), 1.4),
    "tada": ("Tadã", _chord([(523.3, 0, 5, .3), (659.3, .08, 5, .3), (784, .16, 5, .3),
                             (1046.5, .24, 2.6, .42), (1318.5, .24, 3, .16)]), 1.6),
    # Varredura de frequência para baixo: um "plop" curto.
    "pop": ("Pop", "0.8*min(1,t*250)*sin(2*PI*(1100*t-1800*t*t))*exp(-22*t)", .3),
}


def preset_path(name: str) -> Path:
    if name not in PRESETS:
        raise ValueError("Som desconhecido")
    _, expression, duration = PRESETS[name]
    key = hashlib.sha1(f"{expression}|{duration}".encode()).hexdigest()[:10]
    # Absoluto: o ffmpeg roda dentro da pasta, e um storage relativo ("./storage")
    # viraria storage/cache/sons/storage/cache/sons.
    target = SOUND_DIR.resolve() / f"{name}-{key}.wav"
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp.wav")
        fade = min(.15, duration / 3)
        ffmpeg(["-f", "lavfi", "-i", f"aevalsrc=exprs='{expression}':s=48000:d={duration}",
                "-af", f"afade=t=out:st={duration - fade:.3f}:d={fade:.3f},alimiter=limit=0.9:level=false,"
                       "aformat=sample_rates=48000:channel_layouts=stereo",
                "-c:a", "pcm_s16le", str(temporary)], target.parent, timeout=60)
        temporary.replace(target)
    return target


def add_cues(video: Path, sound: Path, out: Path, duration: float, times: list[float],
             volume: float, directory: Path) -> None:
    """Toca `sound` em cada instante de `times` por cima do áudio do vídeo, sem recodificar a imagem."""
    if not times:
        raise ValueError("Nenhum instante para o som")
    # Um som próprio pode ser longo; na revelação só os primeiros segundos importam.
    base = (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=duration=4,"
            f"asetpts=PTS-STARTPTS,volume={volume:.3f},asplit={len(times)}"
            + "".join(f"[s{i}]" for i in range(len(times))))
    delays = ";".join(f"[s{i}]adelay={round(at * 1000)}:all=1[d{i}]" for i, at in enumerate(times))
    mix = ("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[main];[main]"
           + "".join(f"[d{i}]" for i in range(len(times)))
           + f"amix=inputs={len(times) + 1}:duration=first:normalize=0,alimiter=limit=0.89:level=false[a]")
    ffmpeg(["-i", str(video), "-i", str(sound), "-filter_complex", f"{base};{delays};{mix}",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)],
           directory)
