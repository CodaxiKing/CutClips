"""Gera vídeo sintético + transcrição falsa para testar o pipeline sem Whisper.

O áudio alterna tom/silêncio em ciclos de 5s (3.5s de "fala", 1.5s de pausa),
e a transcrição falsa é construída sobre exatamente esses ciclos. Isso permite
verificar se o corte encosta no silêncio e se a duração final bate.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DURATION = 90
CYCLE = 5.0
SPEECH = 3.5

PHRASES = [
    "Todo mundo comete esse erro no começo.",
    "Eu perdi dois anos antes de entender isso direito.",
    "O número que ninguém te conta é quarenta por cento.",
    "Por que ninguém fala sobre a parte chata?",
    "A verdade é que o algoritmo não liga para você.",
    "Descobri isso testando trezentos vídeos seguidos.",
    "Não existe atalho, existe repetição bem feita.",
    "Na real o problema nunca foi a câmera.",
    "Imagina fazer isso todo dia por seis meses.",
    "O segredo está no primeiro segundo do vídeo.",
    "Percebi que estava otimizando a métrica errada.",
    "Sempre começa pelo gancho, nunca pela introdução.",
    "Meu primeiro vídeo teve doze visualizações.",
    "Hoje isso gera trinta mil por mês.",
    "E o mais engraçado é que quase desisti.",
    "Então a pergunta certa não é essa.",
    "Olha o que aconteceu no terceiro mês.",
    "Ninguém aguenta assistir uma introdução de dez segundos.",
]


def make_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # volume pulsante cria silêncios reais para o silencedetect encontrar
    vol = f"if(lt(mod(t,{CYCLE}),{SPEECH}),0.8,0)"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=240:duration={DURATION}",
        "-filter_complex",
        f"[1:a]volume='{vol}':eval=frame[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def make_transcript(path: Path) -> dict:
    """Uma frase por ciclo, palavras distribuídas dentro da janela de fala."""
    words = []
    n_cycles = int(DURATION // CYCLE)
    for c in range(n_cycles):
        phrase = PHRASES[c % len(PHRASES)]
        tokens = phrase.split()
        base = c * CYCLE + 0.10
        usable = SPEECH - 0.30
        step = usable / len(tokens)
        for i, tok in enumerate(tokens):
            start = base + i * step
            words.append({
                "start": round(start, 3),
                "end": round(start + step * 0.82, 3),
                "text": tok,
                "prob": 0.99,
            })
    data = {"language": "pt", "duration": float(DURATION), "words": words}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures")
    video = out / "sample.mp4"
    make_video(video)
    data = make_transcript(out / "transcript.json")
    print(f"vídeo: {video} ({DURATION}s)")
    print(f"transcrição: {len(data['words'])} palavras, {int(DURATION // CYCLE)} frases")
