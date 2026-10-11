from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _as_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _as_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    # --- V2 ---
    default_provider: str = "deepseek"
    providers_json: str = ""
    daily_budget_usd: float = 0.0
    session_budget_usd: float = 0.0
    budget_action: str = "downgrade"          # downgrade | reject
    escalate_on_failure: bool = True
    failure_window: int = 20
    health_probe_timeout: float = 2.0
    cors_allow_all: bool = False
    extra: dict = field(default_factory=dict)

    def providers(self) -> dict:
        """解析 GATEWAY_PROVIDERS 配置（JSON），叠加内置 deepseek。"""
        out = {
            "deepseek": {
                "base_url": self.deepseek_base_url,
                "api_key": self.deepseek_api_key,
                "default_model": self.default_model,
                "responses_path": "/responses",
                "chat_path": "/chat/completions",
            }
        }
        if self.providers_json.strip():
            try:
                extra = json.loads(self.providers_json)
            except json.JSONDecodeError:
                extra = {}
            if isinstance(extra, dict):
                for name, cfg in extra.items():
                    if not isinstance(cfg, dict):
                        continue
                    out[str(name)] = {
                        "base_url": str(cfg.get("base_url", "")).rstrip("/"),
                        "api_key": str(cfg.get("api_key", "")),
                        "default_model": str(cfg.get("default_model", self.default_model)),
                        "responses_path": str(cfg.get("responses_path", "/responses")),
                        "chat_path": str(cfg.get("chat_path", "/chat/completions")),
                    }
        return out


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
        default_provider=os.getenv("GATEWAY_DEFAULT_PROVIDER", "deepseek").strip() or "deepseek",
        providers_json=os.getenv("GATEWAY_PROVIDERS", ""),
        daily_budget_usd=_as_float("GATEWAY_DAILY_BUDGET_USD", 0.0),
        session_budget_usd=_as_float("GATEWAY_SESSION_BUDGET_USD", 0.0),
        budget_action=(os.getenv("GATEWAY_BUDGET_ACTION", "downgrade").strip().lower() or "downgrade"),
        escalate_on_failure=_as_bool("GATEWAY_ESCALATE_ON_FAILURE", True),
        failure_window=_as_int("GATEWAY_FAILURE_WINDOW", 20),
        health_probe_timeout=_as_float("GATEWAY_HEALTH_PROBE_TIMEOUT", 2.0),
        cors_allow_all=_as_bool("GATEWAY_CORS_ALLOW_ALL", False),
    )
