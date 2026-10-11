"""共享测试夹具：注入 MockTransport 上游 + 临时 SQLite。"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_gateway.app import app  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@contextlib.asynccontextmanager
async def gateway(handler):
    """以上游 MockTransport 启动网关，返回 ASGI 客户端。"""
    factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)  # noqa: E731
    app.state.client_factory = factory
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
                yield client
    finally:
        app.state.client_factory = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://upstream.test")
    monkeypatch.setenv("GATEWAY_DEFAULT_MODEL", "deepseek-flash")
    for name in ("GATEWAY_PROVIDERS", "GATEWAY_DEFAULT_PROVIDER", "GATEWAY_DAILY_BUDGET_USD",
                 "GATEWAY_SESSION_BUDGET_USD", "GATEWAY_BUDGET_ACTION", "GATEWAY_ESCALATE_ON_FAILURE",
                 "GATEWAY_FAILURE_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def usage_row(cost: float = 0.0, success: int = 1, latency: int = 100, session_id: str = "default") -> dict:
    return {
        "request_id": "seed", "provider": "deepseek", "model": "deepseek-flash", "effort": "high",
        "stream": 0, "input_tokens": 100, "cached_tokens": 0, "output_tokens": 10,
        "cost_usd": cost, "latency_ms": latency, "success": success, "retry_count": 0,
        "error_type": None, "routed_reason": "seed", "upstream_model": "deepseek-flash",
        "error_body_hash": None, "session_id": session_id, "budget_decision": "ok",
    }
