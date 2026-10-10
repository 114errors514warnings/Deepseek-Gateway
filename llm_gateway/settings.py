from __future__ import annotations

import os
from dataclasses import dataclass


def _as_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _as_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_base_url: str
    db_path: str
    timeout_seconds: int
    default_model: str
    input_cache_hit_per_m: float
    input_cache_miss_per_m: float
    output_per_m: float


def load_settings() -> Settings:
    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        db_path=os.getenv("GATEWAY_DB_PATH", "storage/usage.db"),
        timeout_seconds=_as_int("GATEWAY_TIMEOUT_SECONDS", 300),
        default_model=os.getenv("GATEWAY_DEFAULT_MODEL", "deepseek-flash"),
        input_cache_hit_per_m=_as_float("GATEWAY_INPUT_CACHE_HIT_PER_M", 0.006),
        input_cache_miss_per_m=_as_float("GATEWAY_INPUT_CACHE_MISS_PER_M", 0.3),
        output_per_m=_as_float("GATEWAY_OUTPUT_PER_M", 1.2),
    )

