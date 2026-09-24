"""Vídeo narrado a partir de um tema: roteiro, voz, imagens e legenda.

É o formato "faceless" — nenhuma câmera, nenhuma fonte para cortar. A IA escreve
o roteiro e os termos de busca, a voz do sistema narra, as imagens vêm de um
banco, e a legenda sai da própria narração.

Três decisões que fogem do óbvio:

* A legenda **não** vem do roteiro escrito, e sim de transcrever a narração
  gerada. O texto escrito não sabe onde a voz respirou; a transcrição sabe, e é
  ela que faz a legenda cair junto com a palavra falada.
* A voz é local: a do Windows (SAPI) por padrão, ou o Piper neural quando
  `CUTCLIPS_PIPER_BIN` e `CUTCLIPS_PIPER_MODEL` apontam para o binário e o
  modelo baixados. Nos dois casos roda sem chave, sem custo e sem mandar o
  roteiro para fora — do mesmo jeito que o resto do aplicativo.
* Sem chave do Pexels o modo não morre: cai para a pasta de fundos e, se ela
  estiver vazia, para uma cor lisa. O vídeo sai mais pobre, mas sai.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import backgrounds
from .config import CONFIG, Config, STORAGE
from .montage import MusicOptions, add_music, ffmpeg, music_path, normalize
from .probe import probe

# Palavras por segundo de narração, para estimar a duração antes de sintetizar.
_WORDS_PER_SECOND = 2.6
PEXELS_ENDPOINT = "https://api.pexels.com/videos/search"
# Perfil da voz da influencer: a voz que toda narração usa quando não escolhe outra.
VOICE_FILE = Path(STORAGE) / "voice.json"


class Narration(MusicOptions):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=3, max_length=200)
    # Roteiro pronto. Vazio = a IA escreve a partir do tema.
    script: str = Field(default="", max_length=4000)
    sentences: int = Field(default=6, ge=2, le=20)
    voice: str = Field(default="", max_length=80)
    voice_rate: int = Field(default=0, ge=-10, le=10)
    footage: Literal["auto", "pexels", "folder", "color"] = "auto"
    footage_terms: str = Field(default="", max_length=200)
    background_color: str = Field(default="0x12161C", pattern=r"^0x[0-9A-Fa-f]{6}$")
    orientation: Literal["vertical", "feed", "square", "horizontal"] = "vertical"
    captions: bool = True

    @field_validator("subject", "script", "footage_terms")
    @classmethod
    def tidy(cls, value):
        return " ".join(value.split())


# --------------------------------------------------------------------------- #
# Roteiro
# --------------------------------------------------------------------------- #

SYSTEM = """Você escreve roteiros de vídeos verticais curtos, narrados em off.

Regras:
- Português do Brasil, falado, frases curtas. Nada de listas, títulos ou marcação.
- A primeira frase é o gancho: precisa prender em menos de 2 segundos.
- Uma ideia por frase. Sem "olá", "seja bem-vindo", "não esqueça de se inscrever".
- Só afirmações que se sustentam; na dúvida, escreva algo mais genérico e correto.
- Termos de busca: palavras concretas e visuais, em inglês, que encontrem vídeo de
  banco de imagens. "city at night" funciona; "sensação de liberdade" não.

Devolva SOMENTE JSON válido, sem cercas de código:
{"script": ["frase 1", "frase 2"], "terms": ["...", "..."],
 "title": "...", "description": "...", "hashtags": ["#..."]}"""


def write_script(spec: Narration, cfg: Config = CONFIG) -> dict:
    """Roteiro, termos de busca e metadados. Sem IA, devolve o mínimo viável."""
    from .select import PROVIDERS, ProviderError, _extract_json
    provider = PROVIDERS.get(cfg.llm_provider)
    if provider is None:
        raise ProviderError(f"Provedor de IA desconhecido: {cfg.llm_provider}")
    user = (f"Tema: {spec.subject}\nQuantidade: {spec.sentences} frases\n"
            f"Termos de busca: {max(3, spec.sentences // 2)}")
    data = _extract_json(provider(SYSTEM, user, cfg))
    script = [" ".join(str(x).split()) for x in (data.get("script") or []) if str(x).strip()]
    if not script:
        raise ValueError("A IA não devolveu roteiro")
    terms = [str(x).strip() for x in (data.get("terms") or []) if str(x).strip()]
    return {"script": script[:spec.sentences], "terms": terms[:8],
            "title": str(data.get("title") or spec.subject)[:100],
            "description": str(data.get("description") or "")[:800],
            "hashtags": [str(x) for x in (data.get("hashtags") or [])][:8]}


SELL_SYSTEM = """Você escreve roteiros de vendas curtos, falados em voz alta por um influencer, para vídeos verticais.

Regras:
- Português do Brasil, falado, frases curtas. Entre 3 e 4 frases, ~15 segundos no total.
- Frase 1: gancho citando o produto. Frase 2: benefício pessoal real. Frase 3: chamada para comprar ou ver o link.
- Nada de listas, markdown, emojis ou "seja bem-vindo".

Devolva SOMENTE JSON válido, sem cercas de código:
{"script": ["frase 1", "frase 2", "frase 3"]}"""

# Sem chave a geração não pode falhar: o vídeo inteiro já rodou minutos até aqui.
SELL_FALLBACK_CTA = "Se você curtiu, o link tá na vitrine."


def sell_script(product: str, benefit: str = "", cta: str = "", cfg: Config = CONFIG) -> list[str]:
    """Roteiro de venda do one-shot: IA quando há provedor pronto, template fixo quando não.

    Diferente de write_script, nunca levanta exceção por falta de IA — o fallback
    é parte do contrato, não um erro. Quem escreveu à mão passa `script` direto.
    """
    try:
        from .quiz_ai import ai_status
        from .select import PROVIDERS, _extract_json
        if ai_status(cfg)["ready"]:
            provider = PROVIDERS.get(cfg.llm_provider)
            user = (f"Produto: {product}\nBenefício: {benefit or '(não informado)'}\n"
                    f"Chamada para ação: {cta or '(use uma chamada padrão)'}")
            data = _extract_json(provider(SELL_SYSTEM, user, cfg))
            lines = [" ".join(str(x).split()) for x in (data.get("script") or []) if str(x).strip()]
            if lines:
                return lines[:6]
    except Exception:
        pass  # provedor sem chave ou fora do ar: o template leva o vídeo a término
    lines = [f"Olha só o que chegou: {product}."]
    lines.append(f"Desde que comecei a usar, {benefit}." if benefit.strip()
                 else "A qualidade me surpreendeu de verdade.")
    lines.append((cta or SELL_FALLBACK_CTA).strip())
    return lines


REPLY_SYSTEM = """Você responde comentários de vídeos curtos como se fosse o próprio influencer falando.

Regras:
- Português do Brasil, falado, frases curtas. Entre 2 e 4 frases, ~10 segundos.
- Agradeça de forma natural, responda o que a pessoa perguntou ou comentou e feche convidando a continuar a conversa.
- Comentário negativo ou agressivo: responda com educação, sem briga e sem ironia.
- Nada de listas, markdown, emojis ou "seja bem-vindo".

Devolva SOMENTE JSON válido, sem cercas de código:
{"script": ["frase 1", "frase 2"]}"""

# Sem IA, ainda assim se responde: agradecer nunca depende de chave.
REPLY_FALLBACK = ["Valeu pelo comentário, ficou bom demais!",
                  "Sempre leio o que vocês mandam aqui, continue comentando.",
                  "Se quiserem ver mais, é só seguir e ficar de olho nos próximos vídeos."]


def reply_script(comments: str, cfg: Config = CONFIG) -> list[str]:
    """Resposta falada a comentários: IA quando há provedor pronto, template quando não."""
    try:
        from .quiz_ai import ai_status
        from .select import PROVIDERS, _extract_json
        if ai_status(cfg)["ready"]:
            provider = PROVIDERS.get(cfg.llm_provider)
            user = f"Comentários recebidos:\n{comments[:1500]}"
            data = _extract_json(provider(REPLY_SYSTEM, user, cfg))
            lines = [" ".join(str(x).split()) for x in (data.get("script") or []) if str(x).strip()]
            if lines:
                return lines[:6]
    except Exception:
        pass  # sem chave ou provedor fora do ar: o template agradecido responde
    return list(REPLY_FALLBACK)


# --------------------------------------------------------------------------- #
# Voz
# --------------------------------------------------------------------------- #

def audio_duration(path: Path) -> float:
    """Duração de um arquivo só de áudio. `probe` exige trilha de vídeo."""
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", str(path)],
                          capture_output=True, text=True, timeout=60)
    try:
        return float(done.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Não foi possível medir a narração: {path.name}") from exc


def list_voices() -> list[str]:
    """Vozes instaladas no Windows, com a do Piper na frente quando ele está pronto."""
    script = ("Add-Type -AssemblyName System.Speech;"
              "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()"
              " | ForEach-Object { $_.VoiceInfo.Name }")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, timeout=30)
        voices = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        voices = []
    status = piper_status()
    return [status["voice"], *voices] if status["ready"] else voices


# --------------------------------------------------------------------------- #
# Voz neural local (Piper)
# --------------------------------------------------------------------------- #

# Configurar os dois env vars é escolher o Piper: ele vira a primeira voz da
# lista e o padrão de quem não escolheu nada. Quem digita um nome SAPI explícito
# continua com o SAPI.
PIPER_PREFIX = "Piper · "
# Variante de flag que funcionou nesta instalação: o clássico usa sublinhado
# (--output_file) e o piper1-gpl usa hífen (--output-file).
_piper_flags: tuple[str, str] | None = None


def piper_status() -> dict:
    """Estado do Piper local: pronto, o que falta e o nome da voz que ele oferece."""
    binary = os.getenv("CUTCLIPS_PIPER_BIN", "").strip()
    model = os.getenv("CUTCLIPS_PIPER_MODEL", "").strip()
    missing = []
    if not binary or not Path(binary).is_file():
        missing.append("CUTCLIPS_PIPER_BIN")
    if not model or not Path(model).is_file():
        missing.append("CUTCLIPS_PIPER_MODEL")
    return {"ready": not missing, "missing": missing,
            "voice": PIPER_PREFIX + (Path(model).stem if model else "voz local")}


def piper_active(voice: str) -> bool:
    """Esta fala sai pelo Piper: quem escolheu a voz do Piper, ou ninguém escolheu
    voz nenhuma e o Piper está configurado."""
    status = piper_status()
    if not status["ready"]:
        return False
    return not voice.strip() or voice.startswith(PIPER_PREFIX)


def _length_scale(rate: int) -> str:
    """Andamento −10..10 (a mesma escala do SAPI) para o length-scale do Piper."""
    rate = int(max(-10, min(10, rate)))
    return str(round(1 - 0.04 * rate, 3) if rate >= 0 else round(1 - 0.06 * rate, 3))


def _speak_piper(text: str, out_wav: Path, rate: int) -> Path:
    """Síntese neural local. O texto vai por stdin, não por linha de comando:
    roteiro tem aspas, acento e quebra de linha, e argumento é como isso quebra."""
    global _piper_flags
    status = piper_status()
    if not status["ready"]:
        raise RuntimeError("Piper não está configurado: " + ", ".join(status["missing"]))
    binary = os.getenv("CUTCLIPS_PIPER_BIN", "").strip()
    model = os.getenv("CUTCLIPS_PIPER_MODEL", "").strip()
    source = out_wav.with_suffix(".txt")
    source.write_text(text, encoding="utf-8")
    styles = [_piper_flags] if _piper_flags else [("output_file", "length_scale"),
                                                  ("output-file", "length-scale")]
    detail = ""
    for style in styles:
        out_wav.unlink(missing_ok=True)
        try:
            with source.open("rb") as feed:
                done = subprocess.run([binary, "--model", model,
                                       f"--{style[0]}", str(out_wav),
                                       f"--{style[1]}", _length_scale(rate)],
                                      stdin=feed, capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            detail = str(exc)
            continue
        if not done.returncode and out_wav.is_file() and out_wav.stat().st_size >= 1024:
            _piper_flags = style
            return out_wav
        detail = (done.stderr or done.stdout or "").strip()[-300:] or detail
    raise RuntimeError(f"O Piper não gerou o áudio. {detail or 'Verifique o binário e o modelo.'}")


def speak(text: str, out_wav: Path, voice: str = "", rate: int = 0) -> Path:
    """Narração em WAV: Piper neural local quando configurado, senão a voz do sistema.

    O texto vai por arquivo (SAPI) ou stdin (Piper), nunca por linha de comando:
    roteiro tem aspas, acento e quebra, e passar isso por argumento é como o
    comando quebra em produção.
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if piper_active(voice):
        return _speak_piper(text, out_wav, rate)
    source = out_wav.with_suffix(".txt")
    source.write_text(text, encoding="utf-8")
    # Voz ausente não pode derrubar a geração: o nome muda entre máquinas e entre
    # SAPI de 32 e 64 bits. Sem a voz pedida, narra com a padrão do sistema.
    pick = (f"try {{ $s.SelectVoice('{voice}') }} catch {{ }};"
            if voice and "'" not in voice else "")
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              f"{pick}$s.Rate = {int(max(-10, min(10, rate)))};"
              f"$s.SetOutputToWaveFile('{out_wav.resolve()}');"
              f"$s.Speak([IO.File]::ReadAllText('{source.resolve()}',"
              "[Text.Encoding]::UTF8));$s.SetOutputToNull();$s.Dispose()")
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=600)
    if done.returncode or not out_wav.is_file() or out_wav.stat().st_size < 1024:
        detail = (done.stderr or done.stdout or "").strip()[-300:]
        raise RuntimeError("Não foi possível gerar a narração com a voz do sistema. "
                           f"{detail or 'Verifique se há voz instalada no Windows.'}")
    return out_wav


def influencer_voice() -> dict:
    """Perfil salvo da voz da influencer. Vazio = ainda não foi definida."""
    try:
        data = json.loads(VOICE_FILE.read_text(encoding="utf-8"))
        rate = int(data.get("rate") or 0)
        saved = data.get("saved_at")
        return {"voice": str(data.get("voice") or ""), "rate": max(-10, min(10, rate)),
                "saved_at": float(saved) if saved else None}
    except (OSError, ValueError, TypeError):
        return {"voice": "", "rate": 0, "saved_at": None}


def save_influencer_voice(voice: str, rate: int) -> dict:
    """Grava a voz escolhida como a da influencer. Vazio = volta ao padrão do sistema.

    O perfil é um JSON simples no storage porque é dado de preferência, não de job:
    não nasce nem morce com nenhuma geração, e a API de narração o aplica sozinha
    quando o pedido não escolher voz.
    """
    rate = int(max(-10, min(10, rate)))
    if not voice.strip():
        VOICE_FILE.unlink(missing_ok=True)
        return {"voice": "", "rate": 0, "saved_at": None}
    payload = {"voice": voice.strip(), "rate": rate, "saved_at": time.time()}
    VOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOICE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"voice": payload["voice"], "rate": rate, "saved_at": payload["saved_at"]}


# --------------------------------------------------------------------------- #
# Imagens
# --------------------------------------------------------------------------- #

def search_pexels(term: str, minimum: float, want: int = 3) -> list[str]:
    """URLs de vídeo do Pexels para um termo. Sem chave, devolve lista vazia."""
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    query = urllib.parse.urlencode({"query": term, "per_page": max(3, want * 2),
                                    "orientation": "portrait"})
    request = urllib.request.Request(f"{PEXELS_ENDPOINT}?{query}", headers={"Authorization": key})
    try:
        with urllib.request.urlopen(request, timeout=25) as answer:
            payload = json.loads(answer.read())
    except Exception:
        return []
    picked: list[str] = []
    for video in payload.get("videos", []):
        if (video.get("duration") or 0) < minimum:
            continue
        files = [f for f in video.get("video_files", []) if f.get("link") and f.get("width")]
        if not files:
            continue
        # O maior arquivo que ainda não é absurdo: acima de 4K só custa download.
        usable = [f for f in files if (f.get("width") or 0) <= 2160] or files
        picked.append(max(usable, key=lambda f: f.get("width") or 0)["link"])
        if len(picked) >= want:
            break
    return picked


def fetch_clip(url: str, destination: Path) -> Path | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as answer, destination.open("wb") as out:
            while chunk := answer.read(1 << 20):
                out.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        return None
    return destination if destination.stat().st_size > 10_000 else None


def gather_footage(spec: Narration, terms: list[str], duration: float, folder: Path) -> list[Path]:
    """Vídeos de fundo, na ordem de preferência que o modo permite."""
    if spec.footage == "color":
        return []
    clips: list[Path] = []
    if spec.footage in {"auto", "pexels"}:
        wanted = max(1, int(duration // 6))
        for index, term in enumerate(terms or [spec.subject]):
            for found, link in enumerate(search_pexels(term, 4.0, 2)):
                path = fetch_clip(link, folder / f"pexels-{index}-{found}.mp4")
                if path:
                    clips.append(path)
            if len(clips) >= wanted:
                break
    if not clips and spec.footage in {"auto", "folder"}:
        for item in backgrounds.usable():
            clips.append(backgrounds.background_path(item["name"]))
    return clips


# --------------------------------------------------------------------------- #
# Montagem
# --------------------------------------------------------------------------- #

def transcribe_voice(wav: Path, cache: Path):
    """Separado para os testes trocarem o Whisper por palavras prontas."""
    from .transcribe import transcribe
    return transcribe(wav, cache, Config(language="pt"))


def build_visual(spec: Narration, clips: list[Path], duration: float, width: int, height: int,
                 work: Path) -> str:
    """Trilha de imagem do tamanho da narração, a partir do que houver."""
    if not clips:
        ffmpeg(["-f", "lavfi", "-i",
                f"color=c={spec.background_color}:s={width}x{height}:r=30:d={duration:.3f}",
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-pix_fmt", "yuv420p", "-an", "visual.mp4"], work)
        return "visual.mp4"
    # Cada trecho cobre uma fatia igual; o último absorve a sobra do arredondamento.
    each = max(2.0, duration / len(clips))
    parts: list[str] = []
    for index, clip in enumerate(clips):
        info = probe(clip)
        span = min(each, max(2.0, info.duration))
        if index == len(clips) - 1:
            span = max(2.0, duration - sum(p[1] for p in [(0, each)] * index))
        start = backgrounds.random_start(info.duration, span)
        name = f"visual-{index}.mov"
        normalize(clip, name, width, height, start, span, work, cover=True,
                  has_audio=info.has_audio, loop=info.duration < span)
        parts.append(name)
    (work / "visual.txt").write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    ffmpeg(["-f", "concat", "-safe", "1", "-i", "visual.txt", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-r", "30", "-an", "visual.mp4"], work)
    return "visual.mp4"


def render_narration(spec: Narration, script: list[str], terms: list[str], directory: Path,
                     progress=lambda *_: None, width: int = 1080, height: int = 1920) -> dict:
    directory = directory.resolve()
    work = directory / "narrate-work"
    clips_dir = directory / "clips"
    work.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    progress("gravando a narração", .30)
    voice_wav = speak(" ".join(script), work / "voice.wav", spec.voice, spec.voice_rate)
    # Meio segundo de sobra no fim: cortar na última sílaba soa amador.
    duration = round(audio_duration(voice_wav) + 0.6, 3)

    progress("buscando imagens", .45)
    footage = gather_footage(spec, terms, duration, work / "footage")
    progress("montando a imagem", .60)
    visual = build_visual(spec, footage, duration, width, height, work)

    subtitle = None
    if spec.captions:
        progress("legendando a narração", .72)
        from .captions import build_ass
        transcript = transcribe_voice(voice_wav, work / "voice-transcript.json")
        if transcript.words:
            caption_cfg = Config(out_width=width, out_height=height, caption_size=76,
                                 caption_position="middle")
            build_ass(transcript.words, 0.0, duration, work / "voice.ass", caption_cfg)
            subtitle = "voice.ass"

    progress("juntando voz e imagem", .84)
    chain = f"[0:v]{'ass=' + subtitle + ',' if subtitle else ''}format=yuv420p[v]"
    spoken = clips_dir / "narration-rendering.mp4"
    ffmpeg(["-i", visual, "-i", str(voice_wav.resolve()), "-filter_complex", chain,
            "-map", "[v]", "-map", "1:a", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(spoken)], work)

    final = clips_dir / "narration.mp4"
    if spec.music:
        progress("aplicando a trilha", .92)
        add_music(spoken, music_path(spec.music), final, duration, spec, work)
        spoken.unlink(missing_ok=True)
    else:
        spoken.replace(final)
    actual = probe(final)
    if abs(actual.duration - duration) > .3 or not actual.has_audio:
        raise RuntimeError("A montagem não passou na verificação de duração e áudio")
    ffmpeg(["-i", str(final), "-ss", "0.5", "-frames:v", "1", "-update", "1",
            str(clips_dir / "narration-cover.jpg")], work)

    warnings: list[str] = []
    if not footage and spec.footage != "color":
        warnings.append("Sem imagens: configure PEXELS_API_KEY ou coloque vídeos na pasta de "
                        "fundos. O vídeo saiu com fundo liso.")
    warnings.append("Voz neural local (Piper). Confira a pronúncia de nomes próprios antes "
                    "de publicar." if piper_active(spec.voice)
                    else "Voz sintetizada pelo Windows. Confira a pronúncia de nomes próprios antes "
                         "de publicar.")
    texto = " ".join(script)
    return {"kind": "narration", "source_duration": duration,
            "source_resolution": f"{width}×{height}", "provider": "narração",
            "subject": spec.subject, "script": script, "terms": terms,
            "footage_count": len(footage),
            "clips": [{"index": 1, "revision": 0, "file": final.name,
                       "thumbnail": "narration-cover.jpg", "title": spec.subject,
                       "text": texto, "description": texto,
                       "actual_duration": actual.duration, "source_start": 0,
                       "source_end": duration, "provider": "narração",
                       "warnings": warnings}]}


def process_narration(settings: dict, directory: Path, progress=lambda *_: None,
                      cfg: Config = CONFIG) -> dict:
    from .config import ORIENTATIONS
    started = time.time()
    spec = Narration.model_validate(settings["narration"])
    width, height = ORIENTATIONS[spec.orientation]

    if spec.script:
        progress("usando o roteiro enviado", .10)
        sentences = [s.strip() for s in spec.script.replace("!", ".").replace("?", ".").split(".")
                     if s.strip()]
        script, terms = sentences[:spec.sentences], []
        metadata = {"title": spec.subject, "description": spec.script, "hashtags": []}
    else:
        progress("escrevendo o roteiro", .10)
        written = write_script(spec, cfg)
        script, terms = written["script"], written["terms"]
        metadata = {k: written[k] for k in ("title", "description", "hashtags")}
    if spec.footage_terms:
        terms = [t.strip() for t in spec.footage_terms.split(",") if t.strip()]

    manifest = render_narration(spec, script, terms, directory, progress, width, height)
    manifest["metadata"] = metadata
    clip = manifest["clips"][0]
    clip["title"] = metadata.get("title") or spec.subject
    if metadata.get("description"):
        tags = " ".join(metadata.get("hashtags") or [])
        clip["description"] = (metadata["description"] + ("\n\n" + tags if tags else ""))
    manifest["elapsed_seconds"] = round(time.time() - started, 2)
    return manifest
