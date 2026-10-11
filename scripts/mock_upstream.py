"""本地 mock 上游：用于在不需要真实 DeepSeek key 的情况下做网关端到端演示。

模拟网关实际会调用的三个路径：
  GET  /models
  POST /chat/completions
  POST /responses
返回的 usage token 量随网关路由到的档位变化，因此下游的计量/成本是真实产生的。

用法（另开一个终端）：
    python -m uvicorn scripts.mock_upstream:app --host 127.0.0.1 --port 9100
然后把网关指向它：
    $env:DEEPSEEK_API_KEY='demo-key'; $env:DEEPSEEK_BASE_URL='http://127.0.0.1:9100'
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock DeepSeek Upstream")

# 各档位对应的 (输入 token, 输出 token) 画像
PROFILE = {
    "none": (400, 100),
    "low": (800, 200),
    "high": (4000, 800),
    "max": (20000, 2000),
}


@app.get("/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": "deepseek-flash", "object": "model"}]}


@app.post("/chat/completions")
async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    effort = str(body.get("reasoning_effort", "n/a"))
    inp, out = PROFILE.get(effort, (4000, 800))
    return JSONResponse({
        "id": "mock-chat",
        "object": "chat.completion",
        "model": body.get("model", "deepseek-flash"),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"mock reply (effort={effort})"},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": inp,
            "completion_tokens": out,
            "prompt_cache_hit_tokens": int(inp * 0.5),
        },
    })


@app.post("/responses")
async def responses(request: Request) -> JSONResponse:
    body = await request.json()
    effort = str((body.get("reasoning") or {}).get("effort", "n/a"))
    inp, out = PROFILE.get(effort, (4000, 800))
    return JSONResponse({
        "id": "mock-resp",
        "object": "response",
        "model": body.get("model", "deepseek-flash"),
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": f"mock reply (effort={effort})"}],
        }],
        "usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "input_tokens_details": {"cached_tokens": int(inp * 0.5)},
        },
    })
