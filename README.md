# LLM Gateway V1

本项目是面向 DeepSeek Flash 的本地 OpenAI Responses API Gateway。V1 的设计重点是兼容性与可观测性：它不压缩、不重写客户端发送的上下文，也不试图读取客户端未发送的内部提示词。

## 功能

- `POST /v1/responses`：转发 DeepSeek Responses API，供 Codex 使用，支持 SSE 流式响应。
- `POST /v1/chat/completions`：转发 OpenAI Chat Completions，供 Cline 的 OpenAI Compatible provider 使用，支持 SSE 流式响应。
- 规则路由：简单检索/摘要类请求使用 `low`，其他请求默认 `high`，复杂关键词或超大输入使用 `max`。
- 非流式且尚未返回结果的上游失败可从 `low`/`high` 升级为 `max` 重试一次。
- SQLite 记录模型、推理档位、token、缓存命中、时延、状态和错误。
- `GET /health` 和 `GET /metrics` 用于运行状态与汇总查看。

## 安装与启动（Windows PowerShell）

```powershell
Copy-Item .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
.\scripts\start.ps1
```

服务默认监听 `http://127.0.0.1:9000`。首次启动会自动创建 `.venv` 并安装依赖。停止时在运行窗口按 `Ctrl+C`。

## 客户端配置

Codex 首次接入前，需要有 `deepseek-flash` 的模型目录。最稳妥的做法是先按 DeepSeek 官方 Codex 集成说明运行其配置脚本、选择 Flash，让它创建 `~/.codex/models.json`；然后将用户级 `~/.codex/config.toml` 中的 provider 改为本地 Gateway。保留模型目录，但删除官方脚本写入的 `experimental_bearer_token`。

在用户级 `~/.codex/config.toml` 使用下面的相关配置（不要放在项目 `.codex/config.toml`）：

```toml
model_provider = "local_gateway"
model = "deepseek-flash"
model_reasoning_effort = "high"
model_catalog_json = "~/.codex/models.json"
preferred_auth_method = "apikey"
forced_login_method = "api"
web_search = "disabled"

[model_providers.local_gateway]
name = "Local DeepSeek Gateway"
base_url = "http://127.0.0.1:9000/v1"
env_key = "GATEWAY_API_KEY"
wire_api = "responses"
```

在启动 Codex 的终端设置 `GATEWAY_API_KEY`（这是 Codex provider 的凭据来源；V1 仅监听本机回环地址，不校验其值），例如：

```powershell
$env:GATEWAY_API_KEY = "local-development"
```

不要把 provider 配置放进项目 `.codex/config.toml`：Codex 会忽略该位置的 provider 配置。

### Cline

在 Cline 设置中选择 **OpenAI Compatible**，填入：

```text
Base URL: http://127.0.0.1:9000/v1
API Key: 任意非空本地值
Model ID: deepseek-flash
```

Cline 使用 `/v1/chat/completions`；其自身继续负责项目上下文、工具结果和会话压缩，Gateway 只代理、路由和计量。

其他 OpenAI Responses 或 Chat Completions 兼容客户端的 Base URL 同样设为 `http://127.0.0.1:9000/v1`，模型名使用 `deepseek-flash`。

## 运维接口

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health
Invoke-RestMethod http://127.0.0.1:9000/metrics
```

SQLite 文件默认位于 `storage/usage.db`。V1 没有 Dashboard；先用 `/metrics` 和数据库确认真实成本、缓存命中与失败率，再决定是否值得开发图形界面。

## V1 边界

- 流式请求一旦开始返回 SSE，不能安全地自动重试，因此仅透传并记录。
- Gateway 无法看到工具执行后的测试/补丁结果，不能据此升级；该能力属于后续客户端或 MCP 回传集成。
- 重试只针对上游 HTTP 5xx、连接/超时错误及显式 `failed` 状态；不会重试正常完成的回答。
