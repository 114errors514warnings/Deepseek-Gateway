"""离线评测：成本 A/B、误路由率、计量完整性。"""
from __future__ import annotations

from pathlib import Path

from llm_gateway.evaluation import (
    USAGE_SAMPLES, baseline_cost, case_cost, evaluate_cost_ab, load_workload, metering_completeness,
)
from llm_gateway.settings import Settings

CASES = Path(__file__).resolve().parents[1] / "eval" / "routing_cases.jsonl"


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="k", deepseek_base_url="https://u", db_path=":memory:",
        timeout_seconds=5, default_model="m",
        input_cache_hit_per_m=0.006, input_cache_miss_per_m=0.3, output_per_m=1.2,
    )


def test_load_workload_covers_all_categories() -> None:
    cases = load_workload(CASES)
    assert len(cases) == 30
    assert {c.category for c in cases} == {"simple", "normal", "complex"}
    assert all(c.input_tokens > 0 and c.output_tokens > 0 for c in cases)


def test_cost_ab_saves_at_least_15_percent() -> None:
    result = evaluate_cost_ab(_settings(), load_workload(CASES))
    assert result["cost_saving_rate"] >= 0.15
    assert result["smart_cost_usd"] < result["baseline_cost_usd"]


def test_no_false_routes_on_labeled_set() -> None:
    result = evaluate_cost_ab(_settings(), load_workload(CASES))
    assert result["false_route_rate"] == 0.0
    assert result["misroutes"] == []


def test_metering_completeness_full() -> None:
    assert metering_completeness(USAGE_SAMPLES) == 1.0
    assert metering_completeness([]) == 1.0


def test_case_cost_scales_with_effort() -> None:
    cases = load_workload(CASES)
    complex_case = next(c for c in cases if c.category == "complex")
    assert case_cost(_settings(), complex_case, "low") < case_cost(_settings(), complex_case, "high")
    assert case_cost(_settings(), complex_case, "high") < case_cost(_settings(), complex_case, "max")
    # 基线为复杂任务计入 high + max 重试
    assert baseline_cost(_settings(), complex_case) > case_cost(_settings(), complex_case, "high")
