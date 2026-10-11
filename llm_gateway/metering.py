"""计量（V2 抽出为独立模块）：用量解析与成本核算。

被 `app.py`（真实请求）与 `evaluation.py`（离线评测）共用，保证「线上/离线同一口径」。
"""
from __future__ import annotations

from typing import Any

from .settings import Settings


def usage_tokens(response: dict[str, Any] | None) -> tuple[int, int, int]:
    """兼容 Responses 与 Chat Completions 两种 usage 结构。

    返回 (input_tokens, cached_tokens, output_tokens)。
    """
    usage = (response or {}).get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cached = usage.get("input_tokens_details", {}).get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens", 0)
    return input_tokens, int(cached or 0), output_tokens


def request_cost(settings: Settings, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """按 USD/百万 token 核算单次请求成本（缓存命中输入单独计价）。"""
    uncached = max(0, input_tokens - cached_tokens)
    return (cached_tokens * settings.input_cache_hit_per_m
            + uncached * settings.input_cache_miss_per_m
            + output_tokens * settings.output_per_m) / 1_000_000
