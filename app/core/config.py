"""Application configuration (pydantic-settings, `.env`).

All tunable thresholds live here and nowhere else (CLAUDE.md §3.7).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- server ---
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8001
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"

    # --- model ---
    MODEL_PATH: str = ""
    MODEL_DEVICE: str = "auto"  # auto | cuda | cpu
    MODEL_DTYPE: str = "auto"  # auto | bfloat16 | float16 | float32
    MODEL_MIN_FULL_GPU_VRAM_GB: float = 9.0
    MODEL_QUANTIZATION: Literal["auto", "none", "4bit", "8bit"] = "auto"  # bitsandbytes; auto -> 4bit when VRAM short
    MODEL_GPU_MAX_MEMORY_GB: float | None = None
    MODEL_MAX_NEW_TOKENS: int = 512
    MODEL_REPETITION_PENALTY: float = 1.05
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_QUEUE: int = 8  # requests waiting for / holding the model; more -> 503 LLM_BUSY

    # --- search model defaults ---
    DEFAULT_SEARCH_ROWS: int = 20
    ACADEMIC_TITLE_ID_DEFAULT: int = 0

    # --- fuzzy matching ---
    FUZZY_AUTO_MATCH_THRESHOLD: float = 88.0
    FUZZY_CANDIDATE_THRESHOLD: float = 75.0
    FUZZY_AMBIGUITY_MARGIN: float = 3.0
    FUZZY_MAX_CANDIDATES: int = 3
    # token_sort_ratio (default) | ratio | WRatio | token_set_ratio — see docs/architecture.md decision log.
    # WRatio / token_set_ratio score subset matches as ~100 ("Toshkent vil." -> Toshkent shahri): silent wrong picks.
    FUZZY_SCORER: Literal["token_sort_ratio", "ratio", "WRatio", "token_set_ratio"] = "token_sort_ratio"

    # --- HR search API ---
    HR_SEARCH_API_URL: str = ""
    HR_SEARCH_API_PATH: str = "/api/search"
    HR_SEARCH_API_KEY: str = ""
    HR_SEARCH_API_TIMEOUT: float = 30.0

    # --- references ---
    REFERENCE_API_BASE_URL: str = ""
    REFERENCE_CACHE_TTL_SECONDS: int = 3600
    REFERENCE_API_TIMEOUT: float = 10.0
    # optional JSON object collection -> path, e.g. {"regions": "/dict/regions"}; default "/<collection>"
    REFERENCE_API_PATHS: dict[str, str] = {}
    LOCAL_REFERENCES_PATH: str = "references/example_references.json"
    # local JSON has placeholder IDs: /search refuses to send them to the HR API unless explicitly allowed
    ALLOW_LOCAL_REFERENCES_FOR_SEARCH: bool = False

    # --- security / limits ---
    AI_SEARCH_API_KEY: str = ""
    CORS_ALLOWED_ORIGINS: str = "http://localhost:4200,http://localhost:3000"
    MAX_QUERY_LENGTH: int = 1000
    MAX_REFERENCE_ITEMS: int = 20000
    MAX_BODY_BYTES: int = 5 * 1024 * 1024

    @field_validator("MODEL_GPU_MAX_MEMORY_GB", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    def resolve_path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
