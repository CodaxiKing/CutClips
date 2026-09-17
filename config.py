"""Configuração central. Tudo sobrescrevível por variável de ambiente."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass
STORAGE = Path(os.getenv("CLIPFORGE_STORAGE", ROOT / "storage"))


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass
class Config:
    # --- transcrição ---
    whisper_model: str = os.getenv("CLIPFORGE_WHISPER_MODEL", "large-v3")
    whisper_device: str = os.getenv("CLIPFORGE_WHISPER_DEVICE", "auto")
    whisper_compute: str = os.getenv("CLIPFORGE_WHISPER_COMPUTE", "default")
    language: str | None = os.getenv("CLIPFORGE_LANGUAGE") or None  # None = autodetect
    diarization: bool = os.getenv("CLIPFORGE_DIARIZATION", "1").lower() not in {"0", "false", "no"}
    # 0 = decidir pela máquina. Sem GPU a transcrição é o trecho mais longo do
    # processamento, e o padrão da biblioteca usa menos núcleos do que existem.
    whisper_threads: int = _i("CLIPFORGE_WHISPER_THREADS", 0)
    # 0 = decidir pelo dispositivo. Busca em feixe é barata na GPU e cara no CPU;
    # medido em fala em português, feixe 5 e feixe 2 deram texto idêntico e o
    # feixe 5 custou um terço a mais de tempo.
    whisper_beam: int = _i("CLIPFORGE_WHISPER_BEAM", 0)

    # --- cortes de transmissão (Twitch, Kick, YouTube) ---
    # Teto da gravação de uma live em andamento. Gravar roda em tempo real:
    # 30 minutos pedidos são 30 minutos de espera.
    live_max_minutes: float = _f("CLIPFORGE_LIVE_MAX_MINUTES", 120.0)
    live_minutes: float = _f("CLIPFORGE_LIVE_MINUTES", 30.0)
    # Acima desta duração o vídeo passa por garimpo antes de transcrever: uma VOD
    # de 6h transcrita inteira custa horas de GPU para aproveitar poucos minutos.
    prospect_after_minutes: float = _f("CLIPFORGE_PROSPECT_AFTER_MINUTES", 25.0)
    # Margem de contexto em volta de cada momento garimpado, em segundos.
    prospect_margin: float = _f("CLIPFORGE_PROSPECT_MARGIN", 25.0)
    orientation: str = "vertical"  # vertical (1080x1920) | horizontal (1920x1080)

    # --- download por URL (YouTube) ---
    # 1080 basta para saída 1080x1920; 2160 dá crop mais nítido, mas baixa e processa mais.
    download_max_height: int = _i("CLIPFORGE_DOWNLOAD_MAX_HEIGHT", 1080)

    # --- seleção de clipes ---
    llm_provider: str = os.getenv("CLIPFORGE_LLM_PROVIDER", "anthropic")  # anthropic|openai|ollama|heuristic
    llm_model: str = os.getenv("CLIPFORGE_LLM_MODEL", "")
    triage_model: str = os.getenv("CLIPFORGE_TRIAGE_MODEL", "")
    review_model: str = os.getenv("CLIPFORGE_REVIEW_MODEL", "")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    max_clips: int = _i("CLIPFORGE_MAX_CLIPS", 10)
    min_duration: float = _f("CLIPFORGE_MIN_DURATION", 20.0)
    max_duration: float = _f("CLIPFORGE_MAX_DURATION", 90.0)
    target_duration: float = _f("CLIPFORGE_TARGET_DURATION", 45.0)
    analysis_block_seconds: float = _f("CLIPFORGE_ANALYSIS_BLOCK_SECONDS", 240.0)
    analysis_context_sentences: int = _i("CLIPFORGE_CONTEXT_SENTENCES", 2)
    shortlist_multiplier: int = _i("CLIPFORGE_SHORTLIST_MULTIPLIER", 3)
    niche: str = ""
    audience: str = ""
    rights_notes: str = ""

    # --- limites de corte ---
    # Margem de respiro antes/depois da fala, em segundos.
    lead_in: float = _f("CLIPFORGE_LEAD_IN", 0.18)
    lead_out: float = _f("CLIPFORGE_LEAD_OUT", 0.35)
    # Janela onde procuramos silêncio para encostar o corte.
    snap_window: float = _f("CLIPFORGE_SNAP_WINDOW", 0.60)
    silence_db: float = _f("CLIPFORGE_SILENCE_DB", -32.0)
    silence_min: float = _f("CLIPFORGE_SILENCE_MIN", 0.12)

    # --- reframe 9:16 ---
    out_width: int = _i("CLIPFORGE_OUT_WIDTH", 1080)
    out_height: int = _i("CLIPFORGE_OUT_HEIGHT", 1920)
    track_fps: float = _f("CLIPFORGE_TRACK_FPS", 4.0)  # amostras de detecção por segundo
    # Suavização do movimento da câmera virtual.
    smooth_seconds: float = _f("CLIPFORGE_SMOOTH_SECONDS", 1.2)
    # Só move a câmera se o alvo sair desta fração da largura do crop (anti-jitter).
    deadzone: float = _f("CLIPFORGE_DEADZONE", 0.12)
    # Câmera confortável: prefere o centro, trava cenas quase estáticas e limita
    # a velocidade do pan. Valores são frações da largura do recorte.
    center_bias: float = _f("CLIPFORGE_CENTER_BIAS", 0.25)
    # Faixa central do recorte onde o assunto precisa ficar. A preferência pelo
    # centro nunca pode empurrá-lo para fora dela: é o que impedia enquadrar quem
    # fala encostado na lateral do quadro.
    safe_area: float = _f("CLIPFORGE_SAFE_AREA", 0.5)
    stationary_threshold: float = _f("CLIPFORGE_STATIONARY_THRESHOLD", 0.18)
    max_pan_speed: float = _f("CLIPFORGE_MAX_PAN_SPEED", 0.32)
    motion_fps: float = _f("CLIPFORGE_MOTION_FPS", 30.0)
    # Desvio tolerado para trocar a perseguição quadro a quadro por uma animação
    # linear: movimento único e contínuo em vez de uma sucessão de correções.
    linear_tolerance: float = _f("CLIPFORGE_LINEAR_TOLERANCE", 0.08)
    # Buraco de detecção maior que isto segura o enquadramento onde estava.
    hold_seconds: float = _f("CLIPFORGE_HOLD_SECONDS", 1.0)
    # Fração mínima de segundos com rosto para confiar no índice do vídeo inteiro.
    min_face_coverage: float = _f("CLIPFORGE_MIN_FACE_COVERAGE", 0.35)
    layout: str = "track"  # track|active|manual|fit|split
    crop_x: float = 0.5
    crop_y: float = 0.5
    secondary_x: float = 0.8

    # --- legendas ---
    caption_font: str = os.getenv("CLIPFORGE_FONT", "DejaVu Sans")
    caption_size: int = _i("CLIPFORGE_CAPTION_SIZE", 78)
    caption_max_words: int = _i("CLIPFORGE_CAPTION_MAX_WORDS", 4)
    caption_primary: str = os.getenv("CLIPFORGE_CAPTION_PRIMARY", "&H00FFFFFF")
    caption_highlight: str = os.getenv("CLIPFORGE_CAPTION_HIGHLIGHT", "&H0000E5FF")
    caption_margin_v: int = _i("CLIPFORGE_CAPTION_MARGIN_V", 420)
    caption_position: str = "bottom"
    caption_style: str = "karaoke"
    captions_enabled: bool = True

    # --- render ---
    crf: int = _i("CLIPFORGE_CRF", 19)
    preset: str = os.getenv("CLIPFORGE_PRESET", "medium")
    video_encoder: str = os.getenv("CLIPFORGE_VIDEO_ENCODER", "auto")  # auto|nvenc|qsv|amf|cpu
    audio_bitrate: str = os.getenv("CLIPFORGE_AUDIO_BITRATE", "192k")
    threads: int = _i("CLIPFORGE_THREADS", 0)
    normalize_audio: bool = True
    denoise_audio: bool = False
    auto_edit: bool = True
    internal_silence_seconds: float = _f("CLIPFORGE_INTERNAL_SILENCE_SECONDS", 1.15)

    storage: Path = field(default_factory=lambda: STORAGE)

    def resolved_model(self) -> str:
        return self.llm_model or {"anthropic": "claude-sonnet-4-5", "openai": "gpt-4o-mini",
                                  "ollama": "llama3.1:8b", "heuristic": ""}[self.llm_provider]

    def resolved_stage_model(self, stage: str) -> str:
        override = self.triage_model if stage == "triage" else self.review_model
        return override or self.resolved_model()

    def validate(self) -> None:
        ranges = {
            "max_clips": (1, 30), "min_duration": (0.5, 180),
            "max_duration": (0.5, 180), "caption_size": (24, 120),
            "caption_max_words": (1, 8), "crop_x": (0, 1), "crop_y": (0, 1),
            "secondary_x": (0, 1), "track_fps": (1, 15), "crf": (0, 51),
            "center_bias": (0, 1), "stationary_threshold": (0, 1),
            "safe_area": (0.1, 1), "hold_seconds": (0, 10),
            "linear_tolerance": (0, 1),
            "min_face_coverage": (0, 1),
            "max_pan_speed": (0.02, 2), "motion_fps": (10, 60),
            "analysis_block_seconds": (120, 360), "analysis_context_sentences": (1, 8),
            "shortlist_multiplier": (1, 8),
            "internal_silence_seconds": (0.7, 5),
            "whisper_threads": (0, 128), "whisper_beam": (0, 10),
            "live_max_minutes": (1, 360), "live_minutes": (0.5, 360),
            "prospect_after_minutes": (1, 600), "prospect_margin": (0, 120),
        }
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} deve estar entre {low} e {high}")
        if self.min_duration > self.max_duration:
            raise ValueError("a duração mínima deve ser menor ou igual à máxima")
        for name, options in {
            "llm_provider": {"anthropic", "openai", "ollama", "heuristic"},
            "layout": {"track", "active", "manual", "fit", "split"},
            "caption_position": {"bottom", "middle", "top"},
            "caption_style": {"karaoke", "plain"},
            "video_encoder": {"auto", "nvenc", "qsv", "amf", "cpu"},
            "orientation": {"vertical", "horizontal"},
        }.items():
            if getattr(self, name) not in options:
                raise ValueError(f"{name} inválido")


ORIENTATIONS = {"vertical": (1080, 1920), "horizontal": (1920, 1080)}


def apply_orientation(cfg: Config, orientation: str) -> Config:
    """Ajusta a resolução de saída ao formato pedido.

    O reenquadramento 9:16 não se aplica ao horizontal: o recorte 16:9 de uma
    fonte 16:9 é o quadro inteiro, e o pipeline segue sem cortar nada.
    """
    if orientation not in ORIENTATIONS:
        raise ValueError("orientação inválida")
    cfg.orientation = orientation
    cfg.out_width, cfg.out_height = ORIENTATIONS[orientation]
    return cfg


CONFIG = Config()
