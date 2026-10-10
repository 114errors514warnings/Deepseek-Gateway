from __future__ import annotations

import json
from typing import Any

COMPLEX_KEYWORDS = ("architecture", "root cause", "complex", "复杂", "架构", "疑难", "深度分析")
SIMPLE_KEYWORDS = ("list files", "search files", "read file", "grep", "summarize", "summary", "格式转换", "文件搜索", "目录读取", "摘要")


def _text_from_input(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, ensure_ascii=False).lower()


def _request_text(payload: dict[str, Any]) -> str:
    """Read either Responses `input` or Chat Completions `messages`."""
    if "input" in payload:
        return _text_from_input(payload["input"])
    return _text_from_input(payload.get("messages", ""))


def classify_request(payload: dict[str, Any]) -> str:
    """Return the requested DeepSeek reasoning effort without modifying input."""
    text = _request_text(payload)
    approximate_chars = len(text)
    if approximate_chars > 240_000 or any(word in text for word in COMPLEX_KEYWORDS):
        return "max"
    if any(word in text for word in SIMPLE_KEYWORDS) and not payload.get("tools"):
        return "low"
    return "high"


def selected_effort(payload: dict[str, Any]) -> str:
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") in {"none", "low", "high", "max"}:
        return str(reasoning["effort"])
    if payload.get("reasoning_effort") in {"none", "low", "high", "max"}:
        return str(payload["reasoning_effort"])
    return classify_request(payload)


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
