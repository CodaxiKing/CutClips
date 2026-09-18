"""CutClips — pipeline de clipes verticais automáticos."""
from .config import CONFIG, Config
from .run import process

__all__ = ["CONFIG", "Config", "process"]
__version__ = "0.1.0"
