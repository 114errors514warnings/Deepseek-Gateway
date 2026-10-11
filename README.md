# DeepSeek Gateway（V1 → V2）

面向 DeepSeek Flash 的本地 OpenAI 兼容网关（默认 `127.0.0.1:9000`）。
V1 的设计重点是兼容性与可观测性：它不压缩、不重写客户端发送的上下文，也不试图读取客户端未发送的内部提示词。
V2 在保持这一边界的前提下，补齐了 **可观测加固（V1.1）** 与 **智能路由 / 预算 / 反馈闭环 / 多 provider / Dashboard**。

## 功能

### 兼容转发（V1 起）
- `POST /v1/responses`：转发 DeepSeek Responses API，供 Codex 使用，支持 SSE 流式响应。
- `POST /v1/chat/completions`：转发 OpenAI Chat Completions，供 Cline 的 OpenAI Compatible provider 使用，支持 SSE 流式响应。
- SQLite 记录模型、推理档位、token、缓存命中、时延、状态和错误。

### V1.1 · 可观测加固
- **所有响应路径**（成功 / 上游 4xx / 上游 5xx / 流式 / 预算拒绝）都带：
  `X-Gateway-Effort`、`X-Gateway-Retry-Count`、`X-Gateway-Request-Id`、`X-Gateway-Provider`、`X-Gateway-Upstream-Model`、`X-Gateway-Budget`。
- SQLite 新增列：`routed_reason`（命中哪条路由规则）、`upstream_model`、`error_body_hash`、`session_id`、`budget_decision`；
  **老库自动迁移**（`ALTER TABLE ADD COLUMN`），无需手工改表。
- `GET /metrics` 增加：`p50/p95_latency_ms`、`cost_per_request_usd`、`retry_rate`、`by_model`、`errors` Top-N、
  `time_series`（按小时的成功率/成本序列）、`recent`、`spend_today_usd`。
- `GET /health` 增加**上游连通性探测**（`upstream_reachable` / `upstream_status` / `upstream_latency_ms`），而不再只查 key。

### V2 · 智能路由 + 预算
- 路由从「关键词规则」升级为**多信号打分**（输入长度、工具数量、消息轮次、任务类型关键词、历史失败反馈），
  输出档位与可审计的 `routed_reason`（如 `len:x l;tools:3;kw:complex`）。
- **预算控制**：每会话 / 每日预算，超预算时按配置**降档**（`max→high→low`，绝不升档）或**拒绝（429）**。
- **反馈闭环**：`POST /v1/feedback` 接入上游工具执行结果；失败会话的后续请求自动升一档（`low→high→max`）。
- **多 provider**：任何 OpenAI 兼容后端都能接入，通过请求头 `X-Gateway-Provider`（或请求体 `provider`）选择。
- **Dashboard**：`GET /dashboard` 单页可视化（成本趋势、档位分布、缓存命中、错误 Top-N、最近请求），无外部依赖。

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
Invoke-RestMethod http://127.0.0.1:9000/health        # 含上游连通性
Invoke-RestMethod http://127.0.0.1:9000/metrics       # 汇总 + 分位数 + 时间序列
Invoke-RestMethod "http://127.0.0.1:9000/v1/usage?limit=20"  # 最近请求
Start-Process http://127.0.0.1:9000/dashboard          # 可视化面板
```

SQLite 文件默认位于 `storage/usage.db`。

### 预算与反馈闭环示例

```powershell
# 环境变量（.env）里设置会话预算与处置动作后重启生效
#   GATEWAY_SESSION_BUDGET_USD=0.5
#   GATEWAY_BUDGET_ACTION=downgrade

# 上报一次工具执行失败，触发该会话后续请求升档
Invoke-RestMethod http://127.0.0.1:9000/v1/feedback -Method Post -ContentType application/json `
  -Body '{"session_id":"my-session","tool_ok":false,"note":"tests failed"}'
```

调用方也可用 `X-Gateway-Session` 头显式声明会话，预算与反馈都按该会话累计。

## 测试与验收

```powershell
python -m pytest -q                     # L1：40 项功能/单元测试
python scripts/run_acceptance.py        # L1 + L2 门禁，产出 reports/acceptance_*.md/.json
```

- **L1**：`tests/` 覆盖存储迁移、分位数、预算降档/拒绝、响应头、重试升级、错误透传、流式、反馈闭环、多 provider。
- **L2**：`eval/routing_cases.jsonl`（30 条带标签请求）离线回放路由函数，`routing_accuracy ≥ 0.85` 为门禁。
- **L3**：经真实客户端 + 真实上游的部署冒烟，需要 `DEEPSEEK_API_KEY` 与网络，离线环境标记为 `NO_GATE`。

`run_acceptance.py` 退出码：`0` = 全 PASS/NO_GATE；`1` = 存在 FAIL（可直接用作 CI 门禁）。

## 效果量化（实测）

最近一次全量执行（2026-10-11，报告 `reports/acceptance_20261011-213351.md`）：

| 门禁 | 实测 | 目标 | 判定 |
|---|---|---|---|
| L1 功能/单元测试 | **40 passed** | exit == 0 | PASS |
| 路由准确率 | **1.0000**（30/30） | ≥ 0.85 | PASS |
| 成本节省率（模型化，相对全 high） | **40.69%**（$0.2088 → $0.12384） | ≥ 0.15 | PASS |
| 误路由率 | **0.00%** | ≤ 0.02 | PASS |
| 计量完整性 | **1.0000**（7 种 usage 结构） | ≥ 0.99 | PASS |

端到端真实调用示例（响应头 + `/metrics` + `/v1/usage` + 预算降档/拒绝）见
[`reports/EFFECT_EVIDENCE.md`](reports/EFFECT_EVIDENCE.md)：

- 同一句话的简单请求，无失败历史时路由 `low`，上报工具失败后同会话升到 `high`（`routed_reason=kw:simple;boost:feedback`）。
- 成本按档位分层：`low=$0.0003624`、`high=$0.001572`、`max=$0.00546`（每请求）。
- 会话预算超限：降档模式下第二次 `max→high`，拒绝模式下第二次返回 `429`。

## 边界

- 流式请求一旦开始返回 SSE，不能安全地自动重试，因此仅透传并记录（`X-Gateway-Retry-Count` 恒为 0）。
- Gateway 看不到工具执行结果本身，需由调用方通过 `/v1/feedback` 上报，才能形成「失败 → 升档」闭环。
- 重试只针对上游 HTTP 5xx、连接/超时错误及显式 `failed` 状态；不会重试正常完成的回答。
- 路由是**离线可解释的规则打分**，不是学习模型；升级为分类器属于 Phase 4（按证据触发）。
