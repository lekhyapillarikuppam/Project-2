"""Central configuration. Every limit and path in the app comes from here (env-driven)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    version: str = "0.1.0"

    cors_origins: str = "http://localhost:5173"

    # Storage
    storage_dir: Path = Path("storage")
    retention_hours: int = 24
    cleanup_interval_minutes: int = 10

    # Upload limits
    max_file_mb: int = 50
    max_pdf_pages: int = 200
    max_words: int = 100_000
    min_words: int = 20
    scanned_page_min_chars: int = 30
    scanned_page_ratio: float = 0.5

    # Jobs
    max_active_jobs: int = 1
    max_queued_jobs: int = 3
    expired_stub_minutes: int = 60  # an expired job answers JOB_EXPIRED this long, then JOB_NOT_FOUND

    # TTS
    tts_primary: str = "edge"
    tts_fallback: Literal["piper", "none"] = "piper"  # Render: none (D26)
    edge_voice: str = "en-US-AriaNeural"
    piper_model_path: Path = Path("models/en_US-lessac-medium.onnx")
    piper_disable_arena: bool = True  # onnxruntime CPU memory arena + memory pattern off (lower peak)
    piper_max_chars: int = 300  # Piper input is split into pieces this long (peak grows with length)
    tts_concurrency: int = 4
    tts_chunk_max_chars: int = 1500
    tts_max_retries: int = 3
    tts_fallback_after_consecutive_failures: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def storage_path(self) -> Path:
        p = self.storage_dir
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def piper_model_full_path(self) -> Path:
        p = self.piper_model_path
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
