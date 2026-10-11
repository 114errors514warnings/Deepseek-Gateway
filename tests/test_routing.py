"""V2 路由：离线回放带标签请求，测路由准确率（不真调上游）。"""
from __future__ import annotations

import json
from pathlib import Path

from llm_gateway.routing import (
    classify_request,
    collect_signals,
    plan_route,
    route_request,
    selected_effort,
    selected_route,
)

_CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "routing_cases.jsonl"
CASES = [json.loads(line) for line in _CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

ROUTING_ACCURACY_GATE = 0.85


def _payload(case: dict) -> dict:
    payload = {k: v for k, v in case.items() if k in ("input", "messages", "tools")}
    return payload


def test_routing_accuracy_meets_gate() -> None:
    hits = 0
    misses = []
    for case in CASES:
        decision = selected_route(_payload(case))
        if decision.effort == case["expected"]:
            hits += 1
        else:
            misses.append((case["id"], case["expected"], decision.effort, decision.reason))
    accuracy = hits / len(CASES)
    assert accuracy >= ROUTING_ACCURACY_GATE, f"路由准确率 {accuracy:.3f} 低于门禁 {ROUTING_ACCURACY_GATE}；misses={misses}"


def test_route_decision_has_reason_and_signals() -> None:
    decision = route_request({"input": "分析这个项目的整体架构"})
    assert decision["effort"] == "max"
    assert decision["reason"]
    assert decision["signals"]["complex_keyword"] is True


def test_explicit_effort_wins() -> None:
    payload = {"input": "分析整体架构", "reasoning": {"effort": "low"}}
    assert selected_route(payload).effort == "low"
    assert selected_route(payload).reason == "explicit:client"
    assert selected_effort({"input": "x", "reasoning_effort": "max"}) == "max"


def test_classify_request_backward_compatible() -> None:
    for case in CASES:
        effort = classify_request(_payload(case))
        assert effort in {"none", "low", "high", "max"}


def test_failure_boost_escalates_one_level() -> None:
    simple = {"input": "list files in src"}
    assert plan_route(simple).effort == "low"
    assert plan_route(simple, failure_boost=True).effort == "high"
    assert "boost:feedback" in plan_route(simple, failure_boost=True).reason
    # max 不再升级，避免越界
    assert plan_route({"input": "整体架构重构方案设计"}, failure_boost=True).effort == "max"


def test_tools_suppress_simple_downgrade() -> None:
    payload = {"input": "search files for parser", "tools": [{"type": "function"}]}
    assert plan_route(payload).effort == "high"


def test_extra_large_input_routes_to_max() -> None:
    payload = {"input": "x" * 250_000}
    assert collect_signals(payload)["chars"] >= 250_000
    assert plan_route(payload).effort == "max"
