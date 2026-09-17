"""Download de vídeo por URL (YouTube, Twitch, Kick) via yt-dlp.

Roda no worker, nunca na API: baixar um VOD de 6h leva minutos e não pode
segurar o request de criação do job.

Transmissão em andamento é caso à parte. Não dá para "baixar" algo que ainda não
terminou: o que se faz é gravar uma janela a partir da borda ao vivo. O yt-dlp
resolve o endereço do stream e o ffmpeg grava os N minutos pedidos.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
from typing import Callable
from urllib.parse import urlparse

from .config import CONFIG, Config

# Lista fechada de hosts. Aceitar qualquer URL faria o worker buscar endereços
# arbitrários a partir de um campo de texto da web.
PLATFORM_HOSTS: dict[str, set[str]] = {
    "youtube": {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
        "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
    },
    "twitch": {
        "twitch.tv", "www.twitch.tv", "m.twitch.tv", "go.twitch.tv", "clips.twitch.tv",
    },
    "kick": {
        "kick.com", "www.kick.com",
    },
}

PLATFORM_NAMES = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}

# Nome amigável do que o link aponta, só para mensagem de erro e manifesto.
KIND_NAMES = {"video": "vídeo", "clip": "clipe", "live": "transmissão ao vivo"}


class DownloadError(RuntimeError):
    pass


def platform_of(url: str) -> str | None:
    """Plataforma do link, ou None se o host não está na lista."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    for platform, hosts in PLATFORM_HOSTS.items():
        if host in hosts:
            return platform
    return None


def is_supported_url(url: str) -> bool:
    return platform_of(url) is not None


def is_youtube_url(url: str) -> bool:
    return platform_of(url) == "youtube"


def describe_url(url: str) -> str:
    platform = platform_of(url)
    if platform is None:
        return "link não suportado"
    lowered = url.lower()
    if "/clip" in lowered or platform == "twitch" and "clips.twitch.tv" in lowered:
        return f"clipe do {PLATFORM_NAMES[platform]}"
    return f"vídeo do {PLATFORM_NAMES[platform]}"


def _base_opts(cfg: Config, dest_dir: Path, hook: Callable[[dict], None]) -> dict:
    h = cfg.download_max_height
    opts = {
        # vídeo + áudio separados (melhor qualidade), com fallback para arquivo único
        "format": f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
    }
    # Codex and some sandbox launchers expose a loopback:9 sentinel to block
    # network access. It is not an actual user proxy. yt-dlp otherwise reads it
    # from the environment and reports an opaque connection failure.
    proxy = next((os.getenv(name) for name in ("HTTPS_PROXY", "ALL_PROXY", "HTTP_PROXY")
                  if os.getenv(name)), "")
    if re.fullmatch(r"https?://(?:127[.]0[.]0[.]1|localhost):9/?", proxy, re.I):
        opts["proxy"] = ""
    return opts


def _live_format(info: dict, cfg: Config) -> dict:
    """Melhor variante com áudio e vídeo juntos dentro do limite de altura.

    Transmissão ao vivo chega como HLS, e as variantes já vêm muxadas. Pegar a
    muxada evita sincronizar dois streams ao vivo durante a gravação.
    """
    formats = [f for f in (info.get("formats") or [])
               if f.get("url") and f.get("vcodec", "none") != "none"]
    muxed = [f for f in formats if f.get("acodec", "none") != "none"] or formats
    within = [f for f in muxed if (f.get("height") or 0) <= cfg.download_max_height]
    pool = within or muxed
    if not pool:
        raise DownloadError("a transmissão não expôs nenhuma variante de vídeo")
    return max(pool, key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)))


def record_live(
    url: str,
    dest_dir: str | Path,
    minutes: float,
    cfg: Config = CONFIG,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, dict]:
    """Grava `minutes` minutos a partir da borda de uma transmissão em andamento.

    Devolve (caminho do arquivo, metadados). O ffmpeg copia os pacotes sem
    recodificar, então a gravação custa quase nada de CPU e roda em tempo real:
    30 minutos de live levam 30 minutos.
    """
    import yt_dlp

    if not is_supported_url(url):
        raise DownloadError(f"URL não suportada: {url}")
    seconds = max(30.0, min(float(minutes) * 60.0, cfg.live_max_minutes * 60.0))
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(cfg, dest_dir, lambda d: None)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).removeprefix("ERROR: ").strip()
        raise DownloadError(f"falha ao ler a transmissão: {msg}") from exc
    if not info.get("is_live"):
        raise DownloadError("esse link não está ao vivo agora")

    chosen = _live_format(info, cfg)
    out = dest_dir / f"{info.get('id') or 'live'}-{int(seconds)}s.mp4"
    headers = {**(info.get("http_headers") or {}), **(chosen.get("http_headers") or {})}
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd += [
        "-i", chosen["url"],
        "-t", f"{seconds:.3f}",
        # copiar os pacotes mantém a gravação em tempo real mesmo em 1080p60
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout or []:
            if on_progress and line.startswith("out_time_ms="):
                try:
                    on_progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / seconds))
                except ValueError:
                    pass
    finally:
        proc.wait()
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        detail = (proc.stderr.read() if proc.stderr else "")[-800:]
        raise DownloadError(f"gravação da transmissão falhou: {detail.strip() or 'sem saída'}")
    meta = {**info, "recorded_seconds": seconds, "was_live_capture": True}
    return out, meta


def download(
    url: str,
    dest_dir: str | Path,
    cfg: Config = CONFIG,
    on_progress: Callable[[float], None] | None = None,
    live_minutes: float = 0.0,
) -> tuple[Path, dict]:
    """Baixa o vídeo para dest_dir. Devolve (caminho do mp4, metadados do yt-dlp).

    `live_minutes` maior que zero autoriza gravar uma transmissão em andamento.
    """
    import yt_dlp

    if not is_supported_url(url):
        raise DownloadError(f"URL não suportada (YouTube, Twitch ou Kick): {url}")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = _base_opts(cfg, dest_dir, hook)
    platform = PLATFORM_NAMES[platform_of(url) or "youtube"]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            # process=False: não expande playlist/canal (seria percorrer todos os vídeos)
            info = ydl.extract_info(url, download=False, process=False)
            # link com &list= ou canal vira redirecionamento; segue sem expandir
            for _ in range(3):
                if info.get("_type") not in ("url", "url_transparent"):
                    break
                info = ydl.extract_info(info["url"], download=False, process=False)
            if info.get("_type") in ("playlist", "multi_video", "url", "url_transparent"):
                raise DownloadError("link de playlist ou canal: cole o link de um vídeo só")
            if info.get("is_live"):
                if live_minutes > 0:
                    return record_live(url, dest_dir, live_minutes, cfg, on_progress)
                raise DownloadError(
                    "o link está ao vivo agora: use a aba de lives para gravar uma janela, "
                    "ou espere o VOD ficar disponível"
                )
            info = ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).removeprefix("ERROR: ").strip()
        raise DownloadError(f"falha ao baixar do {platform}: {msg}") from exc

    downloads = info.get("requested_downloads") or []
    path = Path(downloads[0]["filepath"]) if downloads else None
    if path is None or not path.exists():
        raise DownloadError("yt-dlp terminou sem gerar arquivo")
    return path, info
