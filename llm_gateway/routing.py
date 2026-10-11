"""请求路由（V1 规则版 → V2 多信号打分版）。

对外保持向后兼容：
- `classify_request(payload)` 仍返回 "low"/"high"/"max"；
- `selected_effort(payload)` 仍优先采纳客户端显式指定的 effort；
- `prepare_upstream_payload` / `prepare_chat_payload` 签名不变。

V2 新增：
- `route_request(payload, failure_boost=False)` 返回完整决策（effort / reason / score / signals），
  路由依据（reason）会写进 SQLite，便于审计与 Dashboard 展示。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

EFFORT_LEVELS = ("none", "low", "high", "max")

# 复杂信号：架构分析、根因定位、疑难排查等
COMPLEX_KEYWORDS = (
    "architecture", "root cause", "complex", "refactor", "design doc", "migration",
    "复杂", "架构", "疑难", "深度分析", "根因", "重构", "方案设计", "性能优化", "跨模块",
)
# 简单信号：检索、读取、摘要、格式转换等
SIMPLE_KEYWORDS = (
    "list files", "search files", "read file", "grep", "find file", "summarize", "summary",
    "translate", "rename", "文件搜索", "目录读取", "列出文件", "读取文件", "摘要", "总结", "格式转换", "重命名",
)

# 打分阈值（先跑基线，再据此定「值得升级一档」的幅度）
MAX_SCORE = 2.0
LOW_SCORE = -1.0


@dataclass
class RouteDecision:
    effort: str
    reason: str
    score: float
    signals: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"effort": self.effort, "reason": self.reason, "score": round(self.score, 3), "signals": self.signals}


def _text_from_input(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, ensure_ascii=False).lower()


def _request_text(payload: dict[str, Any]) -> str:
    """Read either Responses `input` or Chat Completions `messages`."""
    if "input" in payload:
        return _text_from_input(payload["input"])
    return _text_from_input(payload.get("messages", ""))


def collect_signals(payload: dict[str, Any]) -> dict[str, Any]:
    """从请求体中抽取路由信号（纯本地、无副作用、不修改 payload）。"""
    text = _request_text(payload)
    tools = payload.get("tools")
    tool_count = len(tools) if isinstance(tools, list) else 0
    messages = payload.get("messages")
    message_count = len(messages) if isinstance(messages, list) else 0
    complex_hit = any(word in text for word in COMPLEX_KEYWORDS)
    simple_hit = any(word in text for word in SIMPLE_KEYWORDS)
    return {
        "chars": len(text),
        "tool_count": tool_count,
        "message_count": message_count,
        "complex_keyword": complex_hit,
        "simple_keyword": simple_hit,
    }


def score_signals(signals: dict[str, Any]) -> tuple[float, list[str]]:
    """把信号映射为分数与可读的命中原因列表。"""
    score = 0.0
    reasons: list[str] = []

    chars = int(signals.get("chars", 0))
    if chars > 240_000:
        score += 2.0
        reasons.append("len:xl")
    elif chars > 60_000:
        score += 1.0
        reasons.append("len:l")
    elif chars > 12_000:
        score += 0.4
        reasons.append("len:m")

    tool_count = int(signals.get("tool_count", 0))
    if tool_count:
        score += min(tool_count, 8) * 0.25
        reasons.append(f"tools:{tool_count}")

    if int(signals.get("message_count", 0)) > 20:
        score += 0.4
        reasons.append("msgs:many")

    if signals.get("complex_keyword"):
        score += 2.5
        reasons.append("kw:complex")

    # 简单关键词只有在「没有工具、没有复杂信号、整体很短」时才降档
    if (signals.get("simple_keyword") and not signals.get("complex_keyword")
            and tool_count == 0 and chars < 4000):
        score -= 2.5
        reasons.append("kw:simple")

    return score, reasons


def _effort_from_score(score: float) -> str:
    if score >= MAX_SCORE:
        return "max"
    if score <= LOW_SCORE:
        return "low"
    return "high"


def plan_route(payload: dict[str, Any], failure_boost: bool = False) -> RouteDecision:
    """V2 路由核心：多信号打分 → effort，并给出 routed_reason。"""
    signals = collect_signals(payload)
    score, reasons = score_signals(signals)
    effort = _effort_from_score(score)
    if failure_boost and effort != "max":
        # 上游工具执行失败后的闭环升级：low → high → max
        idx = EFFORT_LEVELS.index(effort) if effort in EFFORT_LEVELS else EFFORT_LEVELS.index("high")
        effort = EFFORT_LEVELS[min(idx + 1, len(EFFORT_LEVELS) - 1)]
        reasons.append("boost:feedback")
    return RouteDecision(
        effort=effort,
        reason=";".join(reasons) or "default:high",
        score=score,
        signals=signals,
    )


def route_request(payload: dict[str, Any], failure_boost: bool = False) -> dict[str, Any]:
    """便捷入口：返回可直接落库/返回给调用方的决策字典。"""
    return plan_route(payload, failure_boost=failure_boost).as_dict()


def _explicit_effort(payload: dict[str, Any]) -> str | None:
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") in EFFORT_LEVELS:
        return str(reasoning["effort"])
    if payload.get("reasoning_effort") in EFFORT_LEVELS:
        return str(payload["reasoning_effort"])
    return None


def classify_request(payload: dict[str, Any]) -> str:
    """向后兼容：仅返回打分路由的档位（忽略客户端显式指令）。"""
    return plan_route(payload).effort


def selected_effort(payload: dict[str, Any], failure_boost: bool = False) -> str:
    """采纳客户端显式 effort；否则使用打分路由。"""
    explicit = _explicit_effort(payload)
    if explicit:
        return explicit
    return plan_route(payload, failure_boost=failure_boost).effort


def selected_route(payload: dict[str, Any], failure_boost: bool = False) -> RouteDecision:
    """与 selected_effort 同逻辑，但返回完整决策（含 reason）。"""
    explicit = _explicit_effort(payload)
    if explicit:
        return RouteDecision(effort=explicit, reason="explicit:client", score=0.0,
                             signals=collect_signals(payload))
    return plan_route(payload, failure_boost=failure_boost)


def prepare_upstream_payload(payload: dict[str, Any], default_model: str, effort: str) -> dict[str, Any]:
    """Copy the client payload and add only provider-required routing fields."""
    upstream = dict(payload)
    upstream["model"] = upstream.get("model") or default_model
    reasoning = dict(upstream.get("reasoning") or {})
    reasoning["effort"] = effort
    upstream["reasoning"] = reasoning
    return upstream


def prepare_chat_payload(payload: dict[str, Any], default_model: str, effort: str) -> dict[str, Any]:
    """Copy a Chat Completions payload and add DeepSeek's effort field."""
    upstream = dict(payload)
    upstream["model"] = upstream.get("model") or default_model
    upstream["reasoning_effort"] = effort
    return upstream
