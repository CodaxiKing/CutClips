"""Configuração central. Tudo sobrescrevível por variável de ambiente."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
ROOT = _MODULE_DIR if (_MODULE_DIR / "api").is_dir() and (_MODULE_DIR / "web").is_dir() else _MODULE_DIR.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

# O projeto se chamava ClipForge: .env e docker-compose antigos ainda usam CLIPFORGE_*.
# O nome novo vence quando os dois existem; o antigo só preenche o que faltar.
LEGACY_ENV_PREFIX, ENV_PREFIX = "CLIPFORGE_", "CUTCLIPS_"
for _name, _value in list(os.environ.items()):
    if _name.startswith(LEGACY_ENV_PREFIX):
        os.environ.setdefault(ENV_PREFIX + _name[len(LEGACY_ENV_PREFIX):], _value)
STORAGE = Path(os.getenv("CUTCLIPS_STORAGE", ROOT / "storage"))
if not STORAGE.is_absolute():
    STORAGE = ROOT / STORAGE


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass
class Config:
    # --- transcrição ---
    whisper_model: str = os.getenv("CUTCLIPS_WHISPER_MODEL", "large-v3")
    whisper_device: str = os.getenv("CUTCLIPS_WHISPER_DEVICE", "auto")
    whisper_compute: str = os.getenv("CUTCLIPS_WHISPER_COMPUTE", "default")
    language: str | None = os.getenv("CUTCLIPS_LANGUAGE") or None  # None = autodetect
    diarization: bool = os.getenv("CUTCLIPS_DIARIZATION", "1").lower() not in {"0", "false", "no"}
    # 0 = decidir pela máquina. Sem GPU a transcrição é o trecho mais longo do
    # processamento, e o padrão da biblioteca usa menos núcleos do que existem.
    whisper_threads: int = _i("CUTCLIPS_WHISPER_THREADS", 0)
    # 0 = decidir pelo dispositivo. Busca em feixe é barata na GPU e cara no CPU;
    # medido em fala em português, feixe 5 e feixe 2 deram texto idêntico e o
    # feixe 5 custou um terço a mais de tempo.
    whisper_beam: int = _i("CUTCLIPS_WHISPER_BEAM", 0)

    # --- cortes de transmissão (Twitch, Kick, YouTube) ---
    # Teto da gravação de uma live em andamento. Gravar roda em tempo real:
    # 30 minutos pedidos são 30 minutos de espera.
    live_max_minutes: float = _f("CUTCLIPS_LIVE_MAX_MINUTES", 120.0)
    live_minutes: float = _f("CUTCLIPS_LIVE_MINUTES", 30.0)
    # Acima desta duração o vídeo passa por garimpo antes de transcrever: uma VOD
    # de 6h transcrita inteira custa horas de GPU para aproveitar poucos minutos.
    prospect_after_minutes: float = _f("CUTCLIPS_PROSPECT_AFTER_MINUTES", 25.0)
    # Margem de contexto em volta de cada momento garimpado, em segundos.
    prospect_margin: float = _f("CUTCLIPS_PROSPECT_MARGIN", 25.0)
    orientation: str = "vertical"  # vertical (1080x1920) | horizontal (1920x1080)

    # --- download por URL (YouTube) ---
    # 1080 basta para saída 1080x1920; 2160 dá crop mais nítido, mas baixa e processa mais.
    download_max_height: int = _i("CUTCLIPS_DOWNLOAD_MAX_HEIGHT", 1080)

    # --- seleção de clipes ---
    llm_provider: str = os.getenv("CUTCLIPS_LLM_PROVIDER", "anthropic")  # anthropic|openai|ollama|heuristic
    llm_model: str = os.getenv("CUTCLIPS_LLM_MODEL", "")
    triage_model: str = os.getenv("CUTCLIPS_TRIAGE_MODEL", "")
    review_model: str = os.getenv("CUTCLIPS_REVIEW_MODEL", "")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    max_clips: int = _i("CUTCLIPS_MAX_CLIPS", 10)
    min_duration: float = _f("CUTCLIPS_MIN_DURATION", 20.0)
    max_duration: float = _f("CUTCLIPS_MAX_DURATION", 90.0)
    target_duration: float = _f("CUTCLIPS_TARGET_DURATION", 45.0)
    analysis_block_seconds: float = _f("CUTCLIPS_ANALYSIS_BLOCK_SECONDS", 240.0)
    analysis_context_sentences: int = _i("CUTCLIPS_CONTEXT_SENTENCES", 2)
    shortlist_multiplier: int = _i("CUTCLIPS_SHORTLIST_MULTIPLIER", 3)
    niche: str = ""
    audience: str = ""
    rights_notes: str = ""

    # --- limites de corte ---
    # Margem de respiro antes/depois da fala, em segundos.
    lead_in: float = _f("CUTCLIPS_LEAD_IN", 0.18)
    lead_out: float = _f("CUTCLIPS_LEAD_OUT", 0.35)
    # Janela onde procuramos silêncio para encostar o corte.
    snap_window: float = _f("CUTCLIPS_SNAP_WINDOW", 0.60)
    silence_db: float = _f("CUTCLIPS_SILENCE_DB", -32.0)
    silence_min: float = _f("CUTCLIPS_SILENCE_MIN", 0.12)

    # --- reframe 9:16 ---
    out_width: int = _i("CUTCLIPS_OUT_WIDTH", 1080)
    out_height: int = _i("CUTCLIPS_OUT_HEIGHT", 1920)
    track_fps: float = _f("CUTCLIPS_TRACK_FPS", 4.0)  # amostras de detecção por segundo
    # Suavização do movimento da câmera virtual.
    smooth_seconds: float = _f("CUTCLIPS_SMOOTH_SECONDS", 1.2)
    # Só move a câmera se o alvo sair desta fração da largura do crop (anti-jitter).
    deadzone: float = _f("CUTCLIPS_DEADZONE", 0.12)
    # Câmera confortável: prefere o centro, trava cenas quase estáticas e limita
    # a velocidade do pan. Valores são frações da largura do recorte.
    center_bias: float = _f("CUTCLIPS_CENTER_BIAS", 0.25)
    # Faixa central do recorte onde o assunto precisa ficar. A preferência pelo
    # centro nunca pode empurrá-lo para fora dela: é o que impedia enquadrar quem
    # fala encostado na lateral do quadro.
    safe_area: float = _f("CUTCLIPS_SAFE_AREA", 0.5)
    stationary_threshold: float = _f("CUTCLIPS_STATIONARY_THRESHOLD", 0.18)
    max_pan_speed: float = _f("CUTCLIPS_MAX_PAN_SPEED", 0.32)
    motion_fps: float = _f("CUTCLIPS_MOTION_FPS", 30.0)
    # Tela dividida automática: duas pessoas que não cabem no mesmo recorte são
    # melhor servidas por dois quadros do que por uma câmera indo e voltando.
    auto_split: bool = os.getenv("CUTCLIPS_AUTO_SPLIT", "1").lower() not in {"0", "false", "no"}
    # Fração das amostras com rosto em que o par precisa aparecer separado demais.
    split_coverage: float = _f("CUTCLIPS_SPLIT_COVERAGE", 0.45)
    # Aproximação automática em plano aberto: o recorte encolhe e a escala sobe,
    # como no AutoFlip. Abaixo de `min_zoom` a imagem ampliada perde nitidez.
    auto_zoom: bool = os.getenv("CUTCLIPS_AUTO_ZOOM", "1").lower() not in {"0", "false", "no"}
    min_zoom: float = _f("CUTCLIPS_MIN_ZOOM", 0.65)
    # Altura desejada do rosto em relação à altura do recorte.
    face_target: float = _f("CUTCLIPS_FACE_TARGET", 0.18)
    # Ampliação total tolerada (recorte -> saída). Um 9:16 tirado de 1080p já
    # nasce em 1,78x, então o teto precisa ser absoluto e não relativo.
    max_upscale: float = _f("CUTCLIPS_MAX_UPSCALE", 2.5)
    # Desvio tolerado para trocar a perseguição quadro a quadro por uma animação
    # linear: movimento único e contínuo em vez de uma sucessão de correções.
    linear_tolerance: float = _f("CUTCLIPS_LINEAR_TOLERANCE", 0.08)
    # Buraco de detecção maior que isto segura o enquadramento onde estava.
    hold_seconds: float = _f("CUTCLIPS_HOLD_SECONDS", 1.0)
    # Fração mínima de segundos com rosto para confiar no índice do vídeo inteiro.
    min_face_coverage: float = _f("CUTCLIPS_MIN_FACE_COVERAGE", 0.35)
    layout: str = "track"  # track|active|manual|fit|split
    crop_x: float = 0.5
    crop_y: float = 0.5
    secondary_x: float = 0.8

    # --- legendas ---
    caption_font: str = os.getenv("CUTCLIPS_FONT", "DejaVu Sans")
    caption_size: int = _i("CUTCLIPS_CAPTION_SIZE", 78)
    caption_max_words: int = _i("CUTCLIPS_CAPTION_MAX_WORDS", 4)
    caption_primary: str = os.getenv("CUTCLIPS_CAPTION_PRIMARY", "&H00FFFFFF")
    caption_highlight: str = os.getenv("CUTCLIPS_CAPTION_HIGHLIGHT", "&H0000E5FF")
    caption_margin_v: int = _i("CUTCLIPS_CAPTION_MARGIN_V", 420)
    caption_position: str = "bottom"
    caption_style: str = "karaoke"
    captions_enabled: bool = True

    # --- render ---
    crf: int = _i("CUTCLIPS_CRF", 19)
    preset: str = os.getenv("CUTCLIPS_PRESET", "medium")
    video_encoder: str = os.getenv("CUTCLIPS_VIDEO_ENCODER", "auto")  # auto|nvenc|qsv|amf|cpu
    audio_bitrate: str = os.getenv("CUTCLIPS_AUDIO_BITRATE", "192k")
    threads: int = _i("CUTCLIPS_THREADS", 0)
    normalize_audio: bool = True
    denoise_audio: bool = False
    auto_edit: bool = True
    internal_silence_seconds: float = _f("CUTCLIPS_INTERNAL_SILENCE_SECONDS", 1.15)

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
            "split_coverage": (0, 1), "min_zoom": (0.3, 1), "face_target": (0.05, 0.6),
            "max_upscale": (1, 6),
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
            "orientation": set(ORIENTATIONS),
        }.items():
            if getattr(self, name) not in options:
                raise ValueError(f"{name} inválido")


# Formatos de saída. O reenquadramento usa a proporção daqui, então acrescentar
# um formato basta: o recorte, a câmera e a legenda se ajustam sozinhos.
ORIENTATIONS = {
    "vertical": (1080, 1920),    # 9:16 — Shorts, Reels, TikTok
    "feed": (1080, 1350),        # 4:5 — o que ocupa mais tela no feed do Instagram
    "square": (1080, 1080),      # 1:1 — feed antigo e carrossel
    "horizontal": (1920, 1080),  # 16:9 — YouTube tradicional
}


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
