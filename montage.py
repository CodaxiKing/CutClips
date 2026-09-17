"""Peças comuns das montagens verticais (Top 5, quiz, reação).

As três montagens fazem o mesmo arco: buscar fontes, normalizar cada trecho para
o mesmo formato e juntar tudo com texto temporizado por cima. O que muda é o
roteiro do texto e como os pedaços se arrumam na tela.

Normalizar antes de juntar não é preciosismo: fontes de plataformas diferentes
chegam com resolução, taxa de quadros, proporção e presença de áudio diferentes,
e concatenar isso cru produz dessincronia de som e saltos de cadência.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .config import CONFIG, STORAGE, Config
from .download import DownloadError, _base_opts, download, is_supported_url, platform_of

# Canvas de referência das legendas ASS. O libass escala tudo a partir daqui, o
# que deixa as posições válidas mesmo quando a prévia renderiza menor.
PLAY_W, PLAY_H = 1080, 1920

TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
_TIKTOK_PATH = re.compile(r"/(?:@[\w.\-]+/video/\d+|t/[\w-]+)/?$")
_TIKTOK_SHORT = re.compile(r"/[\w-]+/?$")
_TIKTOK_TRANSIENT = re.compile(r"universal data for rehydration|Unexpected response from webpage request", re.I)
TIKTOK_ATTEMPTS = 4
TIKTOK_RETRY_DELAY = 1.5


def ffmpeg(args, directory, timeout=1800):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                            cwd=directory, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError("Falha ao montar o vídeo: " + result.stderr[-1500:])


def text_literal(text: str) -> str:
    """Neutraliza o que o ASS trataria como comando de formatação."""
    return (str(text).replace("\\", "＼").replace("{", "｛").replace("}", "｝")
            .replace("\r", " ").replace("\n", " "))


def is_tiktok_url(url: str) -> bool:
    try:
        u = urlparse(str(url).strip())
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or u.port or u.fragment:
        return False
    host = (u.hostname or "").lower()
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return bool(_TIKTOK_PATH.fullmatch(u.path))
    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        return bool(_TIKTOK_SHORT.fullmatch(u.path))
    return False


def _tiktok_retry(fetch, action: str):
    import yt_dlp
    for attempt in range(TIKTOK_ATTEMPTS):
        try:
            return fetch()
        except yt_dlp.utils.DownloadError as exc:
            # A página anti-bot do TikTok às vezes volta sem os dados do vídeo;
            # a mesma URL costuma funcionar logo em seguida.
            if attempt + 1 < TIKTOK_ATTEMPTS and _TIKTOK_TRANSIENT.search(str(exc)):
                time.sleep(TIKTOK_RETRY_DELAY * (attempt + 1))
                continue
            from .topfive import readable_download_error
            raise DownloadError(f"Não foi possível {action} do TikTok. Isso pode ocorrer por falha do "
                                "downloader, bloqueio de acesso ou indisponibilidade do vídeo. Detalhe: "
                                + readable_download_error(exc)) from exc


def tiktok_duration(url: str) -> float:
    """Duração em segundos, lida sem baixar o vídeo."""
    import yt_dlp
    opts = _base_opts(Config(), STORAGE, lambda _: None)
    opts.update(allowed_extractors=[r"(?i)^TikTok$", r"(?i)^vm\.tiktok$"], socket_timeout=25, retries=2)

    def fetch():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    info = _tiktok_retry(fetch, "ler o vídeo")
    if info.get("_type") in {"playlist", "multi_video"} or not info.get("duration"):
        raise DownloadError("Use um link de vídeo individual disponível publicamente")
    return float(info["duration"])


def download_tiktok(url: str, directory: Path) -> Path:
    import yt_dlp
    directory.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(Config(), directory, lambda _: None)
    opts.update(allowed_extractors=[r"(?i)^TikTok$", r"(?i)^vm\.tiktok$"],
                socket_timeout=25, retries=2, fragment_retries=2, max_filesize=500*1024**2)

    def reject(info, *, incomplete=False):
        if info.get("is_live"):
            return "Transmissões ao vivo não são aceitas aqui"
        if (info.get("duration") or 0) > 600:
            return "Cada fonte deve ter até 10 minutos"

    opts["match_filter"] = reject

    def fetch():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info or info.get("_type") in {"playlist", "multi_video"}:
                raise DownloadError("Use um link de vídeo individual disponível publicamente")
            found = [Path(x["filepath"]) for x in info.get("requested_downloads", []) if x.get("filepath")]
            return found + [Path(ydl.prepare_filename(info))]

    candidates = _tiktok_retry(fetch, "baixar")
    for path in candidates:
        if (path.is_file() and path.resolve().is_relative_to(directory.resolve())
                and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}):
            return path
    raise DownloadError("Download não gerou um arquivo de vídeo válido")


def fetch_source(url: str, directory: Path, cfg: Config = CONFIG) -> Path:
    """Baixa de qualquer plataforma aceita, escolhendo o caminho certo pelo host."""
    if is_tiktok_url(url):
        return download_tiktok(url, directory)
    if is_supported_url(url):
        path, _ = download(url, directory, cfg=cfg)
        return path
    raise DownloadError("Link não suportado. Aceitamos TikTok, YouTube, Twitch e Kick.")


def normalize(source: Path, out_name: str, width: int, height: int, start: float,
              duration: float, directory: Path, *, cover: bool = True, volume: float = 1.0,
              has_audio: bool = True, fps: int = 30, loop: bool = False) -> None:
    """Grava `out_name` com formato fixo: mesma escala, cadência e trilha de áudio.

    `cover` preenche a área cortando as sobras; caso contrário o vídeo cabe
    inteiro e o resto vira barra preta. Fonte sem áudio ganha silêncio para que a
    junção não desalinhe o som das outras. Com `loop`, a fonte recomeça do início
    quando acaba, em vez de congelar no último quadro.
    """
    inputs = [*(["-stream_loop", "-1"] if loop else []), "-ss", f"{start:.3f}", "-i", str(source.resolve())]
    if not has_audio:
        inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    fit = ("increase" if cover else "decrease")
    video = (f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio={fit},"
             + (f"crop={width}:{height}," if cover
                else f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,")
             + f"setsar=1,fps={fps},tpad=stop_mode=clone:stop_duration=1,"
               f"trim=duration={duration},format=yuv420p[v]")
    track = "0:a" if has_audio else "1:a"
    gain = f"volume={max(0.0, volume):.3f}," if has_audio and abs(volume - 1.0) > 1e-3 else ""
    audio = (f"[{track}]asetpts=PTS-STARTPTS,{gain}"
             f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration}[a]")
    # PCM dentro de MOV evita o silêncio de priming do AAC nas junções.
    ffmpeg([*inputs, "-filter_complex", f"{video};{audio}", "-map", "[v]", "-map", "[a]",
            "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", out_name],
           directory)


def ass_document(styles: list[str], events: list[str],
                 width: int = PLAY_W, height: int = PLAY_H) -> str:
    """Arquivo ASS completo a partir de estilos e diálogos já formatados."""
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
            "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
    body = ("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n")
    return head + "\n".join(styles) + body + "".join(events)


# --------------------------------------------------------------------------- #
# Música de fundo
# --------------------------------------------------------------------------- #

MUSIC_DIR = STORAGE / "music"
MUSIC_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}
MUSIC_NAME = r"^[0-9a-f]{32}\.(?:mp3|m4a|aac|wav|ogg|opus|flac)$"


class MusicOptions(BaseModel):
    """Trilha enviada pela pessoa. `music` é o nome gerado no upload, nunca um caminho."""
    music: str = Field(default="", pattern=r"^$|" + MUSIC_NAME)
    music_volume: float = Field(default=0.35, ge=0.05, le=1, allow_inf_nan=False)
    # Abaixa a música enquanto o vídeo tem som próprio (fala, narração, jogo).
    duck: bool = True


def music_path(name: str) -> Path:
    if not re.fullmatch(MUSIC_NAME, name or ""):
        raise ValueError("Música inválida")
    path = (MUSIC_DIR / name).resolve()
    if not path.is_relative_to(MUSIC_DIR.resolve()) or not path.is_file():
        raise ValueError("Música não encontrada: envie o arquivo de novo")
    return path


def audio_duration(path: Path, minimum: float = 1.0, maximum: float | None = None) -> float:
    """Duração da primeira trilha de áudio; erro se o arquivo não tiver áudio."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_type:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    data = json.loads(out.stdout or "{}") if out.returncode == 0 else {}
    if not data.get("streams"):
        raise ValueError("O arquivo não tem uma trilha de áudio legível")
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration < minimum:
        raise ValueError(f"O áudio precisa ter pelo menos {minimum:g} segundo(s)".replace(".", ","))
    if maximum is not None and duration > maximum:
        raise ValueError(f"O áudio deve ter até {maximum:g} segundos")
    return duration


def add_music(video: Path, music: Path, out: Path, duration: float, options: MusicOptions,
              directory: Path) -> None:
    """Mistura a trilha ao áudio do vídeo sem recodificar a imagem.

    A música repete se for mais curta e some em fade no fim. Com `duck`, um
    compressor guiado pelo áudio do próprio vídeo derruba a música enquanto há
    som — a fala continua inteligível sem ninguém ajustar volume trecho a trecho.
    """
    fade = min(1.5, duration / 4)
    music_chain = (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                   f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
                   f"volume={options.music_volume:.3f},"
                   f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}[music]")
    main = "[0:a]aformat=sample_rates=48000:channel_layouts=stereo"
    if options.duck:
        graph = (f"{music_chain};{main},asplit=2[main][key];"
                 "[music][key]sidechaincompress=threshold=0.02:ratio=12:attack=20:release=400[bed];"
                 "[main][bed]amix=inputs=2:duration=first:normalize=0")
    else:
        graph = f"{music_chain};{main}[main];[main][music]amix=inputs=2:duration=first:normalize=0"
    graph += ",alimiter=limit=0.89:level=false[a]"
    ffmpeg(["-i", str(video), "-stream_loop", "-1", "-i", str(music), "-filter_complex", graph,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)],
           directory)
