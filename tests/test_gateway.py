"""网关端到端（离线）：响应头、重试升级、错误透传、流式、预算、反馈闭环、运维接口。"""
from __future__ import annotations

import httpx

from conftest import gateway, run, usage_row
from llm_gateway.app import app


def _ok(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": []})
    return httpx.Response(200, json={
        "id": "resp_1", "status": "completed",
        "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 20}, "output_tokens": 50},
    })


def test_responses_success_headers_and_metrics(env) -> None:
    async def main():
        async with gateway(_ok) as client:
            r = await client.post("/v1/responses", json={"input": "hello there"},
                                  headers={"x-gateway-session": "s1"})
            assert r.status_code == 200
            assert r.headers["x-gateway-effort"] == "high"
            assert r.headers["x-gateway-retry-count"] == "0"
            assert r.headers["x-gateway-provider"] == "deepseek"
            assert r.headers["x-gateway-upstream-model"] == "deepseek-flash"
            assert r.headers["x-gateway-request-id"]

            metrics = (await client.get("/metrics")).json()
            assert metrics["requests"] == 1
            assert metrics["by_effort"][0]["effort"] == "high"
            assert metrics["recent"][0]["routed_reason"]
            assert metrics["recent"][0]["session_id"] == "s1"
    run(main())


def test_retryable_failure_escalates_to_max(env) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json={"id": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}})

    async def main():
        async with gateway(handler) as client:
            r = await client.post("/v1/responses", json={"input": "简单修改一下"}, headers={"x-gateway-session": "s2"})
            assert r.status_code == 200
            assert calls["n"] == 2
            assert r.headers["x-gateway-effort"] == "max"
            assert r.headers["x-gateway-retry-count"] == "1"
    run(main())


def test_upstream_4xx_is_passed_through_without_retry(env) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request", "type": "invalid_request_error"}})

    async def main():
        async with gateway(handler) as client:
            r = await client.post("/v1/responses", json={"input": "hi"}, headers={"x-gateway-session": "s3"})
            assert r.status_code == 400
            assert r.json()["error"]["message"] == "bad request"
            assert r.headers["x-gateway-effort"]
            assert r.headers["x-gateway-retry-count"] == "0"
            assert calls["n"] == 1
            metrics = (await client.get("/metrics")).json()
            assert metrics["recent"][0]["success"] == 0
            assert metrics["recent"][0]["error_body_hash"]
    run(main())


def test_chat_completions_endpoint(env) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = request.read().decode()
        assert "reasoning_effort" in body
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    async def main():
        async with gateway(handler) as client:
            r = await client.post("/v1/chat/completions",
                                  json={"messages": [{"role": "user", "content": "list files"}],
                                        "stream": False},
                                  headers={"x-gateway-session": "s4"})
            assert r.status_code == 200
            assert r.headers["x-gateway-effort"] == "low"
    run(main())


def test_streaming_relay_sets_headers(env) -> None:
    class _SSE(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"delta":"hi"}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_SSE())

    async def main():
        async with gateway(handler) as client:
            async with client.stream("POST", "/v1/responses",
                                     json={"input": "hi", "stream": True},
                                     headers={"x-gateway-session": "s5"}) as r:
                assert r.status_code == 200
                assert r.headers["x-gateway-effort"]
                body = "".join([chunk async for chunk in r.aiter_text()])
                assert "data:" in body
    run(main())


def test_streaming_upstream_error_has_headers(env) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"upstream down")

    async def main():
        async with gateway(handler) as client:
            r = await client.post("/v1/responses", json={"input": "hi", "stream": True},
                                  headers={"x-gateway-session": "s6"})
            assert r.status_code == 503
            assert r.json()["error"]["type"] == "upstream_error"
            assert r.headers["x-gateway-effort"]
    run(main())


def test_health_reports_upstream_reachable(env) -> None:
    async def main():
        async with gateway(_ok) as client:
            health = (await client.get("/health")).json()
            assert health["status"] == "ok"
            assert health["upstream_reachable"] == "true"
    run(main())


def test_health_reports_unreachable_on_network_error(env) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    async def main():
        async with gateway(handler) as client:
            health = (await client.get("/health")).json()
            assert health["upstream_reachable"] == "false"
            assert health["upstream_error"] == "ConnectError"
    run(main())


def test_budget_reject_returns_429(env, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_SESSION_BUDGET_USD", "0.01")
    monkeypatch.setenv("GATEWAY_BUDGET_ACTION", "reject")

    async def main():
        async with gateway(_ok) as client:
            app.state.store.record(usage_row(cost=0.5, session_id="spend"))
            r = await client.post("/v1/responses", json={"input": "hi"},
                                  headers={"x-gateway-session": "spend"})
            assert r.status_code == 429
            assert r.headers["x-gateway-budget"] == "reject"
            metrics = (await client.get("/metrics")).json()
            assert metrics["recent"][0]["error_type"] == "budget_exceeded"
    run(main())


def test_budget_downgrade_lowers_effort(env, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_SESSION_BUDGET_USD", "0.01")
    monkeypatch.setenv("GATEWAY_BUDGET_ACTION", "downgrade")

    async def main():
        async with gateway(_ok) as client:
            app.state.store.record(usage_row(cost=0.5, session_id="tight"))
            r = await client.post("/v1/responses", json={"input": "分析整体架构并重构方案设计"},
                                  headers={"x-gateway-session": "tight"})
            assert r.status_code == 200
            assert r.headers["x-gateway-effort"] == "high"
            assert r.headers["x-gateway-budget"] == "downgrade"
    run(main())


def test_feedback_arms_escalation(env) -> None:
    async def main():
        async with gateway(_ok) as client:
            fb = await client.post("/v1/feedback", json={"session_id": "sX", "tool_ok": False, "note": "tests failed"})
            assert fb.json()["escalation_armed"] is True
            r = await client.post("/v1/responses", json={"input": "list files in src"},
                                  headers={"x-gateway-session": "sX"})
            # 基线为 low，反馈闭环后升一档到 high
            assert r.headers["x-gateway-effort"] == "high"
            metrics = (await client.get("/metrics")).json()
            assert "boost:feedback" in metrics["recent"][0]["routed_reason"]
    run(main())


def test_dashboard_and_usage_endpoints(env) -> None:
    async def main():
        async with gateway(_ok) as client:
            dash = await client.get("/dashboard")
            assert dash.status_code == 200
            assert "LLM Gateway" in dash.text
            await client.post("/v1/responses", json={"input": "hi"}, headers={"x-gateway-session": "s7"})
            usage = (await client.get("/v1/usage?limit=5")).json()
            assert usage["requests"] and usage["requests"][0]["request_id"]
    run(main())


def test_provider_selection(env, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROVIDERS", '{"local": {"base_url": "https://local.test", "api_key": "k2", "default_model": "local-model"}}')

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "local.test"
        return httpx.Response(200, json={"id": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}})

    async def main():
        async with gateway(handler) as client:
            r = await client.post("/v1/responses", json={"input": "hi", "provider": "local"},
                                  headers={"x-gateway-session": "s8"})
            assert r.status_code == 200
            assert r.headers["x-gateway-provider"] == "local"
            assert r.headers["x-gateway-upstream-model"] == "local-model"
    run(main())
