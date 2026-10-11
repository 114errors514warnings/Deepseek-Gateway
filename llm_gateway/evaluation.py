"""离线评测（V2）：把「智能路由」的收益/风险量化，供门禁判定。

- **成本节省率**：离线用「全 high 基线」对比「智能路由」。基线假设复杂任务在 high 下失败、
  升级 max 重试一次（对应网关 V1 的升级重试行为）；智能路由直接把复杂任务送到 max。
- **误路由率**：应 max 却路由 low 的比例（代理「误路由且失败」）。
- **计量完整性**：对多种 usage 结构解析成功率。

这些是**离线模型化估算**；真实请求 A/B（成本/成功率/延迟）属 L3，需真实上游。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .metering import request_cost, usage_tokens
from .routing import selected_route
from .settings import Settings

# 推理档位对输出 token 的经验倍率（越低档越省），仅用于离线估算
OUTPUT_MULTIPLIER = {"none": 1.0, "low": 0.6, "high": 1.0, "max": 1.6}

# 各类任务的代表性 token 规模（输入, 输出）
TOKEN_PROFILE = {
    "simple": (800, 200),
    "normal": (4000, 800),
    "complex": (20000, 2000),
}


@dataclass
class WorkloadCase:
    id: str
    category: str
    expected: str
    payload: dict
    input_tokens: int
    output_tokens: int


def load_workload(routing_cases_path: str | Path) -> list[WorkloadCase]:
    """从既有路由数据集中派生工作负载（同一套固定数据集 + 固定 token 画像）。"""
    cases: list[WorkloadCase] = []
    for line in Path(routing_cases_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        category = raw.get("category", "normal")
        input_tokens, output_tokens = TOKEN_PROFILE.get(category, TOKEN_PROFILE["normal"])
        payload = {k: v for k, v in raw.items() if k in ("input", "messages", "tools")}
        cases.append(WorkloadCase(
            id=raw["id"], category=category, expected=raw["expected"],
            payload=payload, input_tokens=input_tokens, output_tokens=output_tokens,
        ))
    return cases


def case_cost(settings: Settings, case: WorkloadCase, effort: str) -> float:
    output = case.output_tokens * OUTPUT_MULTIPLIER.get(effort, 1.0)
    return request_cost(settings, case.input_tokens, 0, output)


def baseline_cost(settings: Settings, case: WorkloadCase) -> float:
    """「全部 high」基线：复杂任务在 high 下失败 → 升级 max 重试一次。"""
    cost = case_cost(settings, case, "high")
    if case.expected == "max":
        cost += case_cost(settings, case, "max")
    return cost


def evaluate_cost_ab(settings: Settings, cases: list[WorkloadCase]) -> dict:
    baseline = 0.0
    smart = 0.0
    false_route = 0
    misroutes: list[dict] = []
    per_case: list[dict] = []

    for case in cases:
        effort = selected_route(case.payload).effort
        b = baseline_cost(settings, case)
        s = case_cost(settings, case, effort)
        baseline += b
        smart += s
        is_false_route = case.expected == "max" and effort == "low"
        if is_false_route:
            false_route += 1
            misroutes.append({"id": case.id, "expected": case.expected, "got": effort})
        per_case.append({
            "id": case.id, "category": case.category, "effort": effort,
            "baseline_cost_usd": round(b, 8), "smart_cost_usd": round(s, 8),
        })

    total = len(cases) or 1
    return {
        "baseline_cost_usd": round(baseline, 8),
        "smart_cost_usd": round(smart, 8),
        "cost_saving_rate": (1 - smart / baseline) if baseline else 0.0,
        "false_route_rate": false_route / total,
        "misroutes": misroutes,
        "total": len(cases),
        "per_case": per_case,
    }


def metering_completeness(samples: list[dict]) -> float:
    """usage 解析成功率（≥0.99）。"""
    if not samples:
        return 1.0
    ok = 0
    for sample in samples:
        try:
            input_tokens, cached, output_tokens = usage_tokens(sample)
            assert all(isinstance(v, int) for v in (input_tokens, cached, output_tokens))
            assert input_tokens >= 0 and cached >= 0 and output_tokens >= 0
            ok += 1
        except Exception:  # noqa: BLE001
            continue
    return ok / len(samples)


# 覆盖线上可能出现的 usage 结构（含缺失与缓存字段差异）
USAGE_SAMPLES: list[dict] = [
    {"usage": {"input_tokens": 100, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 20}}},
    {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "prompt_cache_hit_tokens": 30}},
    {"usage": {"input_tokens": 0, "output_tokens": 0}},
    {"usage": {}},
    {},
    {"usage": {"prompt_tokens": "120", "completion_tokens": "40"}},
    {"usage": None},
]
