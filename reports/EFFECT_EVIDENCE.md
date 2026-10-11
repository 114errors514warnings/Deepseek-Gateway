# DeepSeek Gateway · 效果量化与验证证据

> 目的：给出一份**可复现**的方法与**真实执行**后的量化结果，说明网关到底带来了什么效果。
> 日期：2026-10-11 · 环境：Windows / Python 3.14 / 本机回环

---

## 1. 测试方式（三层）

| 层 | 命令 | 内容 | 是否需外部依赖 |
|---|---|---|---|
| L1 | `python -m pytest -q` | 功能/单元测试：存储迁移、分位数、预算降档/拒绝、响应头、重试升级、错误透传、流式、反馈闭环、多 provider | 否（离线） |
| L2 | `python scripts/run_acceptance.py` | 门禁：路由准确率、对冻结基线不回归、模型化成本节省率、误路由率、计量完整性 | 否（离线回放 `eval/routing_cases.jsonl`） |
| L3 | 真实客户端 + 真实上游 | 部署冒烟 + 真实成本/成功率/延迟 A/B | **是**（`DEEPSEEK_API_KEY` + 网络） |

一键门禁：`.\scripts\run_gates.ps1`（退出码 `0` = 全 PASS/NO_GATE，可直接作 CI）。

---

## 2. 本次实测结果（L1 + L2）

**L1**：`python -m pytest -q` → **40 passed in 2.12s**，退出码 `0`。

**L2**：`python scripts/run_acceptance.py` → 结论 **PASS**，退出码 `0`（报告：`reports/acceptance_20261011-213351.md`）。

| 层 | 门禁 | 指标 | 实测 | 目标 | 判定 |
|---|---|---|---|---|---|
| L1 | pytest_suite | 功能/单元测试 | 40 passed | exit == 0 | **PASS** |
| L2 | routing_accuracy | 离线回放路由准确率 | **1.0000**（30/30） | ≥ 0.85 | **PASS** |
| L2 | routing_no_regression | 相对冻结基线不回归 | **Δ = +0.0000** | Δ ≥ −0.02 | **PASS** |
| L2 | cost_saving_rate | 模型化成本节省率 | **0.4069** | ≥ 0.15 | **PASS** |
| L2 | false_route_rate | 应 max 却路由 low 的比例 | **0.0000** | ≤ 0.02 | **PASS** |
| L2 | metering_completeness | usage 解析成功率（7 种结构） | **1.0000** | ≥ 0.99 | **PASS** |
| L3 | deploy_smoke | 真实上游 A/B | — | 成功率不降、p95 ≤ +20%、成本 ↓ | **NO_GATE**（离线） |

> 冻结基线：`baselines/routing_accuracy.json`（accuracy = 1.0，数据集 sha256 `94dd3a0f…cafe5`，只读不重跑）。

### 2.1 成本 A/B（离线模型化）

对同一套 30 条标注负载，比较「全部用 high」基线与「智能路由」：

| 指标 | 数值 |
|---|---|
| 全 high 基线成本 | **$0.2088** |
| 智能路由成本 | **$0.12384** |
| **成本节省率** | **40.69%** |
| 误路由率 | 0.00%（0 例） |

口径与假设见 `reports/STAGE_REPORT.md` §三（档位输出 token 倍率 `low=0.6 / high=1.0 / max=1.6`，属离线估算）。

---

## 3. 端到端调用示例（真实请求 + 报表）

为在不依赖真实 DeepSeek 的前提下验证**完整链路**，用本地 mock 上游模拟 DeepSeek 的 `/models`、`/chat/completions`、`/responses`，其返回的 token 量随被路由到的档位变化（`low=(800,200)`、`high=(4000,800)`、`max=(20000,2000)`），因此下游计量/成本是**真实产生**的。

启动方式（等价于把 `.env` 的 `DEEPSEEK_BASE_URL` 指向本地 mock）：

```powershell
$env:DEEPSEEK_API_KEY='demo-key'
$env:DEEPSEEK_BASE_URL='http://127.0.0.1:9100'
python -m uvicorn llm_gateway.app:app --host 127.0.0.1 --port 9000
```

### 3.1 四个请求的响应头（路由结果）

| 用例 | 请求 | 状态 | `X-Gateway-Effort` | `X-Gateway-Provider` | `X-Gateway-Budget` | `X-Gateway-Retry-Count` |
|---|---|---|---|---|---|---|
| A 简单对话 | `please summarize this file` | 200 | **low** | deepseek | disabled | 0 |
| B 复杂对话 | `analyze the architecture and find the root cause…` | 200 | **max** | deepseek | disabled | 0 |
| C 普通问答 | Responses `explain what this function does` | 200 | **high** | deepseek | disabled | 0 |
| D 失败后同会话的简单请求 | 先 `POST /v1/feedback{"tool_ok":false}`，再发 A 同款 | 200 | **high**（升档） | deepseek | disabled | 0 |

关键点：**相同内容**的简单请求，A 得 `low`、D 得 `high` —— 差别来自会话历史里的「工具失败」反馈，闭环生效，路由原因记录为 `kw:simple;boost:feedback`。

### 3.2 `/metrics` 汇总（4 次请求真实累计）

```json
{
  "requests": 4,
  "successes": 4,
  "success_rate": 1.0,
  "cost_usd": 0.0089664,
  "spend_today_usd": 0.008966,
  "p50_latency_ms": 1.0,
  "p95_latency_ms": 1.0,
  "cache_hit_rate": 0.5,
  "retry_rate": 0.0,
  "cost_per_request_usd": 0.0022416
}
```

按档位拆分（`by_effort`），可见成本被路由明显分层：

```json
[
  { "effort": "low",  "requests": 1, "successes": 1, "cost_usd": 0.0003624 },
  { "effort": "high", "requests": 2, "successes": 2, "cost_usd": 0.003144  },
  { "effort": "max",  "requests": 1, "successes": 1, "cost_usd": 0.00546   }
]
```

> 单次成本按 `(cached×$0.006 + uncached×$0.3 + output×$1.2) / 1e6` 核算：
> low=(400×0.006+400×0.3+200×1.2)/1e6=$0.0003624，high=$0.001572，max=$0.00546 —— 与上表一致。

### 3.3 `/v1/usage` 最近请求（审计明细，截取）

```json
[
  { "effort": "high", "input_tokens": 4000,  "cached_tokens": 2000,  "output_tokens": 800,
    "cost_usd": 0.001572, "success": 1,
    "routed_reason": "kw:simple;boost:feedback", "session_id": "demo-sess",    "budget_decision": "disabled" },
  { "effort": "high", "input_tokens": 4000,  "cached_tokens": 2000,  "output_tokens": 800,
    "cost_usd": 0.001572, "success": 1,
    "routed_reason": "default:high",             "session_id": "ip:127.0.0.1", "budget_decision": "disabled" },
  { "effort": "max",  "input_tokens": 20000, "cached_tokens": 10000, "output_tokens": 2000,
    "cost_usd": 0.00546,  "success": 1,
    "routed_reason": "kw:complex",               "session_id": "ip:127.0.0.1", "budget_decision": "disabled" },
  { "effort": "low",  "input_tokens": 800,   "cached_tokens": 400,   "output_tokens": 200,
    "cost_usd": 0.0003624, "success": 1,
    "routed_reason": "kw:simple",                "session_id": "ip:127.0.0.1", "budget_decision": "disabled" }
]
```

每次请求都能回答：**用了哪个档位、为什么（`routed_reason`）、花了多少钱、属于哪个会话、是否触发预算**。

### 3.4 预算控制（同一会话连发两个复杂请求）

配置：`GATEWAY_SESSION_BUDGET_USD=0.0002`。

**降档模式**（`GATEWAY_BUDGET_ACTION=downgrade`）——第二次请求由 `max` 自动降到 `high`：

```json
[
  { "request": 1, "status": 200, "effort": "max",  "budget": "ok" },
  { "request": 2, "status": 200, "effort": "high", "budget": "downgrade" }
]
```
（第二次上游实际收到的也是 high 档 token 画像 `4000/800`，证明降档真实下发。）

**拒绝模式**（`GATEWAY_BUDGET_ACTION=reject`）——第二次请求直接 429：

```json
[
  { "request": 1, "status": 200, "effort": "max", "budget": "ok" },
  { "request": 2, "status": 429, "effort": "max", "budget": "reject",
    "body": { "detail": "gateway budget exceeded" } }
]
```

---

## 4. 如何复现

```powershell
# 1) L1 + L2（离线，无需 key）
python -m pytest -q
python scripts/run_acceptance.py        # 产出 reports/acceptance_*.md/.json

# 2) 端到端（使用本地 mock 上游，无需真实 key）
#    另起一个终端运行 mock：python -m uvicorn scripts.mock_upstream:app --host 127.0.0.1 --port 9100
$env:DEEPSEEK_API_KEY='demo-key'; $env:DEEPSEEK_BASE_URL='http://127.0.0.1:9100'
python -m uvicorn llm_gateway.app:app --host 127.0.0.1 --port 9000
#    随后按 §3 发请求，或用 dashboard：Start-Process http://127.0.0.1:9000/dashboard

# 3) 真实上游 A/B（L3，需真实 key 与网络）
Copy-Item .env.example .env   # 填 DEEPSEEK_API_KEY
.\scripts\start.ps1
```

---

## 5. 结论

| 维度 | 效果（实测） |
|---|---|
| 正确性 | L1 40/40 通过；L2 全门禁 PASS，退出码 0 |
| 路由质量 | 准确率 **100%**，误路由率 **0%** |
| 成本 | 模型化节省 **40.69%**（相对全 high） |
| 计量可信度 | usage 解析成功率 **100%**（7 种结构） |
| 可观测 | 每个请求带审计头 + SQLite 明细 + `/metrics` + Dashboard |
| 成本可控 | 预算降档 / 拒绝（429）真实生效 |
| 闭环 | 工具失败反馈 → 同会话后续请求升档，真实生效 |
| 待补 | 真实上游 A/B（L3）为 `NO_GATE`，需在具备 key 的环境执行 |
