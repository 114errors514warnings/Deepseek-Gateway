"""计量：usage 解析与成本核算（线上/离线同口径）。"""
from __future__ import annotations

from llm_gateway.metering import request_cost, usage_tokens
from llm_gateway.settings import Settings


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="k", deepseek_base_url="https://u", db_path=":memory:",
        timeout_seconds=5, default_model="m",
        input_cache_hit_per_m=0.006, input_cache_miss_per_m=0.3, output_per_m=1.2,
    )


def test_usage_responses_style() -> None:
    assert usage_tokens({"usage": {"input_tokens": 100, "output_tokens": 50,
                                   "input_tokens_details": {"cached_tokens": 20}}}) == (100, 20, 50)


def test_usage_chat_style() -> None:
    assert usage_tokens({"usage": {"prompt_tokens": 100, "completion_tokens": 50,
                                   "prompt_cache_hit_tokens": 30}}) == (100, 30, 50)


def test_usage_missing_is_zero() -> None:
    assert usage_tokens({}) == (0, 0, 0)
    assert usage_tokens(None) == (0, 0, 0)
    assert usage_tokens({"usage": None}) == (0, 0, 0)


def test_request_cost_matches_manual_math() -> None:
    settings = _settings()
    # 1000 输入（其中 400 命中缓存）, 200 输出
    expected = (400 * 0.006 + 600 * 0.3 + 200 * 1.2) / 1_000_000
    assert abs(request_cost(settings, 1000, 400, 200) - expected) < 1e-12
