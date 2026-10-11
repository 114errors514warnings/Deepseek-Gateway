from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .budget import BudgetManager
from .dashboard import DASHBOARD_HTML
from .metering import request_cost as _cost, usage_tokens as _usage
from .providers import Provider, load_providers, resolve_provider
from .routing import prepare_chat_payload, prepare_upstream_payload, selected_route
from .settings import Settings, load_settings
from .storage import UsageStore


def _retryable_response(response: dict[str, Any]) -> bool:
    return response.get("status") == "failed" or response.get("error") is not None


def _hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _session_id(request: Request) -> str:
    for header in ("x-gateway-session", "x-session-id"):
        value = request.headers.get(header)
        if value:
            return value.strip()[:128]
    client = request.client
    return f"ip:{client.host}" if client and client.host else "default"


def _gateway_headers(effort: str, retry_count: int, request_id: str, provider: str | None = None,
                     upstream_model: str | None = None, budget_decision: str | None = None) -> dict[str, str]:
    headers = {
        "X-Gateway-Effort": effort,
        "X-Gateway-Retry-Count": str(retry_count),
        "X-Gateway-Request-Id": request_id,
    }
    if provider:
        headers["X-Gateway-Provider"] = provider
    if upstream_model:
        headers["X-Gateway-Upstream-Model"] = upstream_model
    if budget_decision:
        headers["X-Gateway-Budget"] = budget_decision
    return headers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    app.state.store = UsageStore(settings.db_path)
    app.state.providers = load_providers(settings)
    app.state.budget = BudgetManager(settings, app.state.store)
    factory = getattr(app.state, "client_factory", None)
    app.state.client = factory() if factory else httpx.AsyncClient(timeout=httpx.Timeout(settings.timeout_seconds))
    yield
    await app.state.client.aclose()


app = FastAPI(title="LLM Gateway V2", version="0.2.0", lifespan=lifespan)


def _log(request_id: str, provider: str, model: str, effort: str, stream: bool, started: float,
         response: dict[str, Any] | None, success: bool, retry_count: int, error_type: str | None,
         routed_reason: str = "", session_id: str = "", budget_decision: str = "disabled",
         error_body: str | None = None) -> None:
    settings: Settings = app.state.settings
    input_tokens, cached_tokens, output_tokens = _usage(response or {})
    app.state.store.record({
        "request_id": request_id, "provider": provider, "model": model, "effort": effort,
        "stream": int(stream), "input_tokens": input_tokens, "cached_tokens": cached_tokens,
        "output_tokens": output_tokens, "cost_usd": _cost(settings, input_tokens, cached_tokens, output_tokens),
        "latency_ms": round((time.perf_counter() - started) * 1000), "success": int(success),
        "retry_count": retry_count, "error_type": error_type,
        "routed_reason": routed_reason, "upstream_model": model,
        "error_body_hash": _hash(error_body), "session_id": session_id,
        "budget_decision": budget_decision,
    })


async def _parse_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Request body must be valid JSON",
                            headers={"X-Gateway-Retry-Count": "0"}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Request body must be a JSON object",
                            headers={"X-Gateway-Retry-Count": "0"})
    return payload


async def _read_body(response: httpx.Response) -> str:
    try:
        data = await response.aread()
    except Exception:  # noqa: BLE001
        return ""
    return data.decode(errors="replace")


async def _proxy(request: Request, kind: str) -> Any:
    """responses / chat 两个端点共用的代理、路由、计量与预算逻辑。"""
    settings: Settings = app.state.settings
    payload = await _parse_payload(request)
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    stream = bool(payload.get("stream"))

    provider_name = (request.headers.get("x-gateway-provider") or payload.get("provider")
                     or settings.default_provider)
    provider: Provider | None = resolve_provider(app.state.providers, provider_name)
    if provider is None or not provider.configured:
        raise HTTPException(503, f"provider '{provider_name}' is not configured",
                            headers=_gateway_headers("unknown", 0, request_id, provider_name))

    session_id = _session_id(request)
    failed_sessions = (app.state.store.recent_failed_sessions(settings.failure_window)
                       if settings.escalate_on_failure else set())
    decision = selected_route(payload, failure_boost=session_id in failed_sessions)
    effort = decision.effort
    routed_reason = decision.reason
    model = str(payload.get("model") or provider.default_model)

    budget = app.state.budget.evaluate(session_id, effort)
    budget_decision = budget.decision
    if budget_decision == "reject":
        routed_reason = f"{routed_reason};{budget.reason}"
        _log(request_id, provider.name, model, effort, stream, time.perf_counter(), None, False, 0,
             "budget_exceeded", routed_reason, session_id, budget_decision, budget.reason)
        raise HTTPException(429, "gateway budget exceeded",
                            headers=_gateway_headers(effort, 0, request_id, provider.name, model, budget_decision))
    if budget_decision == "downgrade":
        effort = budget.allowed_effort
        routed_reason = f"{routed_reason};{budget.reason}"

    build = prepare_upstream_payload if kind == "responses" else prepare_chat_payload
    url = provider.responses_url() if kind == "responses" else provider.chat_url()
    upstream_headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
        "X-Request-ID": request_id,
    }
    started = time.perf_counter()

    if stream:
        upstream = build(payload, provider.default_model, effort)
        try:
            upstream_response = await app.state.client.send(
                app.state.client.build_request("POST", url, headers=upstream_headers, json=upstream), stream=True)
        except httpx.HTTPError as exc:
            _log(request_id, provider.name, model, effort, True, started, None, False, 0,
                 type(exc).__name__, routed_reason, session_id, budget_decision, repr(exc))
            raise HTTPException(502, f"upstream request failed: {type(exc).__name__}",
                                headers=_gateway_headers(effort, 0, request_id, provider.name, model,
                                                         budget_decision)) from exc
        if upstream_response.status_code >= 400:
            body = await _read_body(upstream_response)
            await upstream_response.aclose()
            _log(request_id, provider.name, model, effort, True, started, None, False, 0,
                 f"HTTP_{upstream_response.status_code}", routed_reason, session_id, budget_decision, body)
            return JSONResponse(
                content={"error": {"message": body or "upstream error", "type": "upstream_error"}},
                status_code=upstream_response.status_code,
                headers=_gateway_headers(effort, 0, request_id, provider.name, model, budget_decision))

        async def relay() -> AsyncIterator[bytes]:
            successful = True
            error_type = None
            try:
                async for chunk in upstream_response.aiter_raw():
                    yield chunk
            except httpx.HTTPError as exc:
                successful = False
                error_type = type(exc).__name__
                raise
            finally:
                await upstream_response.aclose()
                _log(request_id, provider.name, model, effort, True, started, None, successful, 0,
                     error_type, routed_reason, session_id, budget_decision)

        return StreamingResponse(
            relay(), status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "text/event-stream"),
            headers=_gateway_headers(effort, 0, request_id, provider.name, model, budget_decision))

    retry_count = 0
    final_response: dict[str, Any] | None = None
    final_error: str | None = None
    final_status = 502
    final_body: str | None = None
    current_effort = effort
    for attempt in range(2):
        upstream = build(payload, provider.default_model, current_effort)
        try:
            result = await app.state.client.post(url, headers=upstream_headers, json=upstream)
            final_status = result.status_code
            parsed = result.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            parsed = None
            final_error = type(exc).__name__
            final_body = repr(exc)
            retryable = True
        else:
            final_response = parsed if isinstance(parsed, dict) else None
            retryable = result.status_code >= 500 or (
                result.status_code < 400 and final_response is not None and _retryable_response(final_response))
            final_error = None if result.status_code < 400 else f"HTTP_{result.status_code}"
            if final_error:
                final_body = json.dumps(final_response, ensure_ascii=False) if final_response else result.text
        if retryable and attempt == 0 and current_effort in {"low", "high"}:
            current_effort = "max"
            retry_count = 1
            continue
        break

    success = final_status < 400 and final_response is not None and not _retryable_response(final_response)
    _log(request_id, provider.name, model, current_effort, False, started, final_response, success,
         retry_count, final_error, routed_reason, session_id, budget_decision, final_body)
    headers = _gateway_headers(current_effort, retry_count, request_id, provider.name, model, budget_decision)
    if final_response is not None:
        return JSONResponse(content=final_response, status_code=final_status, headers=headers)
    raise HTTPException(502, f"upstream request failed: {final_error or 'unknown error'}", headers=headers)


@app.get("/health")
async def health() -> dict[str, Any]:
    settings: Settings = app.state.settings
    provider: Provider | None = resolve_provider(app.state.providers, settings.default_provider)
    configured = bool(provider and provider.configured)
    result: dict[str, Any] = {
        "status": "ok" if configured else "misconfigured",
        "upstream": provider.name if provider else "",
        "api_key_configured": str(configured).lower(),
        "upstream_reachable": "unknown",
    }
    if configured and provider is not None:
        started = time.perf_counter()
        try:
            response = await app.state.client.get(
                f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                timeout=settings.health_probe_timeout,
            )
            # 4xx 也说明网络可达（只是没有该端点/鉴权），只有 5xx 与异常才算不可达
            result["upstream_reachable"] = "true" if response.status_code < 500 else "false"
            result["upstream_status"] = response.status_code
        except httpx.HTTPError as exc:
            result["upstream_reachable"] = "false"
            result["upstream_error"] = type(exc).__name__
        result["upstream_latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    return app.state.store.metrics()


@app.get("/v1/usage")
async def usage(limit: int = 50) -> dict[str, Any]:
    return {"requests": app.state.store.recent_requests(limit)}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


@app.post("/v1/feedback")
async def feedback(request: Request) -> dict[str, Any]:
    """接入上游工具执行结果，形成「失败 → 升级档位」闭环。"""
    body = await _parse_payload(request)
    session_id = str(body.get("session_id") or _session_id(request))
    tool_ok = bool(body.get("tool_ok", True))
    app.state.store.record_feedback({
        "request_id": str(body.get("request_id", "")),
        "session_id": session_id,
        "tool_ok": int(tool_ok),
        "note": str(body.get("note", ""))[:500],
        "error_body_hash": _hash(str(body.get("error", ""))),
    })
    return {"ok": True, "session_id": session_id, "escalation_armed": not tool_ok}


@app.post("/v1/responses")
async def responses(request: Request) -> Any:
    return await _proxy(request, "responses")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI Chat Completions compatibility endpoint for Cline and similar clients."""
    return await _proxy(request, "chat")
