from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .routing import prepare_chat_payload, prepare_upstream_payload, selected_effort
from .settings import Settings, load_settings
from .storage import UsageStore


def _usage(response: dict[str, Any]) -> tuple[int, int, int]:
    usage = response.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cached = usage.get("input_tokens_details", {}).get("cached_tokens")
    cached = cached if cached is not None else usage.get("prompt_cache_hit_tokens", 0)
    return input_tokens, int(cached or 0), output_tokens


def _cost(settings: Settings, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    uncached = max(0, input_tokens - cached_tokens)
    return (cached_tokens * settings.input_cache_hit_per_m + uncached * settings.input_cache_miss_per_m + output_tokens * settings.output_per_m) / 1_000_000


def _retryable_response(response: dict[str, Any]) -> bool:
    return response.get("status") == "failed" or response.get("error") is not None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    app.state.store = UsageStore(settings.db_path)
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(settings.timeout_seconds))
    yield
    await app.state.client.aclose()


app = FastAPI(title="LLM Gateway V1", version="0.1.0", lifespan=lifespan)


def _log(request_id: str, model: str, effort: str, stream: bool, started: float, response: dict[str, Any] | None, success: bool, retry_count: int, error_type: str | None) -> None:
    settings: Settings = app.state.settings
    input_tokens, cached_tokens, output_tokens = _usage(response or {})
    app.state.store.record({
        "request_id": request_id, "provider": "deepseek", "model": model, "effort": effort,
        "stream": int(stream), "input_tokens": input_tokens, "cached_tokens": cached_tokens,
        "output_tokens": output_tokens, "cost_usd": _cost(settings, input_tokens, cached_tokens, output_tokens),
        "latency_ms": round((time.perf_counter() - started) * 1000), "success": int(success),
        "retry_count": retry_count, "error_type": error_type,
    })


@app.get("/health")
async def health() -> dict[str, str]:
    configured = bool(app.state.settings.deepseek_api_key)
    return {"status": "ok" if configured else "misconfigured", "upstream": "deepseek", "api_key_configured": str(configured).lower()}


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    return app.state.store.metrics()


@app.post("/v1/responses")
async def responses(request: Request) -> Any:
    settings: Settings = app.state.settings
    if not settings.deepseek_api_key:
        raise HTTPException(503, "DEEPSEEK_API_KEY is not configured")
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Request body must be a JSON object")

    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    stream = bool(payload.get("stream"))
    effort = selected_effort(payload)
    model = str(payload.get("model") or settings.default_model)
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json", "X-Request-ID": request_id}
    url = f"{settings.deepseek_base_url}/responses"

    if stream:
        upstream = prepare_upstream_payload(payload, settings.default_model, effort)
        try:
            upstream_response = await app.state.client.send(app.state.client.build_request("POST", url, headers=headers, json=upstream), stream=True)
        except httpx.HTTPError as exc:
            _log(request_id, model, effort, True, started, None, False, 0, type(exc).__name__)
            raise HTTPException(502, f"DeepSeek request failed: {type(exc).__name__}") from exc
        if upstream_response.status_code >= 400:
            body = await upstream_response.aread()
            await upstream_response.aclose()
            _log(request_id, model, effort, True, started, None, False, 0, f"HTTP_{upstream_response.status_code}")
            return JSONResponse(content={"error": {"message": body.decode(errors="replace"), "type": "upstream_error"}}, status_code=upstream_response.status_code)

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
                _log(request_id, model, effort, True, started, None, successful, 0, error_type)

        return StreamingResponse(relay(), status_code=upstream_response.status_code, media_type=upstream_response.headers.get("content-type", "text/event-stream"), headers={"X-Gateway-Effort": effort, "X-Request-ID": request_id})

    retry_count = 0
    final_response: dict[str, Any] | None = None
    final_error: str | None = None
    final_status = 502
    current_effort = effort
    for attempt in range(2):
        upstream = prepare_upstream_payload(payload, settings.default_model, current_effort)
        try:
            result = await app.state.client.post(url, headers=headers, json=upstream)
            final_status = result.status_code
            parsed = result.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            parsed = None
            final_error = type(exc).__name__
            retryable = True
        else:
            final_response = parsed if isinstance(parsed, dict) else None
            retryable = result.status_code >= 500 or (result.status_code < 400 and final_response is not None and _retryable_response(final_response))
            final_error = None if result.status_code < 400 else f"HTTP_{result.status_code}"
        if retryable and attempt == 0 and current_effort in {"low", "high"}:
            current_effort = "max"
            retry_count = 1
            continue
        break

    success = final_status < 400 and final_response is not None and not _retryable_response(final_response)
    _log(request_id, model, current_effort, False, started, final_response, success, retry_count, final_error)
    if final_response is not None:
        return JSONResponse(content=final_response, status_code=final_status, headers={"X-Gateway-Effort": current_effort, "X-Gateway-Retry-Count": str(retry_count), "X-Request-ID": request_id})
    raise HTTPException(502, f"DeepSeek request failed: {final_error or 'unknown error'}")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI Chat Completions compatibility endpoint for Cline and similar clients."""
    settings: Settings = app.state.settings
    if not settings.deepseek_api_key:
        raise HTTPException(503, "DEEPSEEK_API_KEY is not configured")
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Request body must be a JSON object")

    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    stream = bool(payload.get("stream"))
    effort = selected_effort(payload)
    model = str(payload.get("model") or settings.default_model)
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json", "X-Request-ID": request_id}
    url = f"{settings.deepseek_base_url}/chat/completions"

    if stream:
        upstream = prepare_chat_payload(payload, settings.default_model, effort)
        try:
            upstream_response = await app.state.client.send(app.state.client.build_request("POST", url, headers=headers, json=upstream), stream=True)
        except httpx.HTTPError as exc:
            _log(request_id, model, effort, True, started, None, False, 0, type(exc).__name__)
            raise HTTPException(502, f"DeepSeek request failed: {type(exc).__name__}") from exc
        if upstream_response.status_code >= 400:
            body = await upstream_response.aread()
            await upstream_response.aclose()
            _log(request_id, model, effort, True, started, None, False, 0, f"HTTP_{upstream_response.status_code}")
            return JSONResponse(content={"error": {"message": body.decode(errors="replace"), "type": "upstream_error"}}, status_code=upstream_response.status_code)

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
                _log(request_id, model, effort, True, started, None, successful, 0, error_type)

        return StreamingResponse(relay(), status_code=upstream_response.status_code, media_type=upstream_response.headers.get("content-type", "text/event-stream"), headers={"X-Gateway-Effort": effort, "X-Request-ID": request_id})

    retry_count = 0
    final_response: dict[str, Any] | None = None
    final_error: str | None = None
    final_status = 502
    current_effort = effort
    for attempt in range(2):
        upstream = prepare_chat_payload(payload, settings.default_model, current_effort)
        try:
            result = await app.state.client.post(url, headers=headers, json=upstream)
            final_status = result.status_code
            parsed = result.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            parsed = None
            final_error = type(exc).__name__
            retryable = True
        else:
            final_response = parsed if isinstance(parsed, dict) else None
            retryable = result.status_code >= 500 or (result.status_code < 400 and final_response is not None and _retryable_response(final_response))
            final_error = None if result.status_code < 400 else f"HTTP_{result.status_code}"
        if retryable and attempt == 0 and current_effort in {"low", "high"}:
            current_effort = "max"
            retry_count = 1
            continue
        break

    success = final_status < 400 and final_response is not None and not _retryable_response(final_response)
    _log(request_id, model, current_effort, False, started, final_response, success, retry_count, final_error)
    if final_response is not None:
        return JSONResponse(content=final_response, status_code=final_status, headers={"X-Gateway-Effort": current_effort, "X-Gateway-Retry-Count": str(retry_count), "X-Request-ID": request_id})
    raise HTTPException(502, f"DeepSeek request failed: {final_error or 'unknown error'}")
