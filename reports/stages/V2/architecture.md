# ContextOptimizer · 架构说明 · V2 智能路由 + 预算 + 反馈闭环

> 配套报告：`reports/stages/V2/report.md`；整机架构图：`.archify/architecture-gateway-v2-20261011-031500/gateway-v2-architecture.html`

## 1. 组件与职责

| 组件 | 职责 | 文件 |
|---|---|---|
| Gateway 编排 | 端点、provider 选择、路由→预算→转发→计量 的统一编排（`_proxy`） | `llm_gateway/app.py` |
| 路由打分 | 多信号 → `effort` + `routed_reason` | `llm_gateway/routing.py` |
| 预算控制 | 每会话/每日预算；降档或拒绝 | `llm_gateway/budget.py` |
| Provider 抽象 | 名称 → base_url / api_key / model / path | `llm_gateway/providers.py` |
| 计量 | usage 解析 + 成本核算（线上/离线同口径） | `llm_gateway/metering.py` |
| 存储 | `llm_usage` + `llm_feedback`；迁移、聚合、分位数、时间序列、预算花费 | `llm_gateway/storage.py` |
| 离线评测 | 成本 A/B、误路由率、计量完整性 | `llm_gateway/evaluation.py` |
| Dashboard | 单页可视化 | `llm_gateway/dashboard.py` |

## 2. 主数据流（一次请求）

```
请求 ─▶ 解析 & provider 选择 ─▶ 路由打分(effort, reason)
                                   │
                                   ▼
                          预算评估 evaluate(session, effort)
                     ┌─ ok ──────┴─ downgrade ─┐   └─ reject ─▶ 429
                     ▼                          ▼
              按 provider 转发上游 ◀────── 降档后的 effort
                     │
        ┌─ 失败(5xx/超时/failed) 且 effort∈{low,high} ─▶ 升 max 重试一次
        │
        └─▶ 计量落库(routed_reason/upstream_model/error_body_hash/…)
                     │
                     ▼
              响应 + X-Gateway-* 头
```

## 3. 反馈闭环（异步）

```
调用方工具执行结果 ──POST /v1/feedback{tool_ok:false}──▶ llm_feedback 表
                                                             │
下次请求 recent_failed_sessions(window) ──────────────▶ 命中 session ⇒ 升一档(boost:feedback)
```

## 4. 关键契约
- **路由契约**：`route_request(payload, failure_boost) -> {effort, reason, score, signals}`；`reason` 落库，可离线回放。
- **预算契约**：`BudgetManager.evaluate(session_id, effort) -> {allowed_effort, decision∈{ok,downgrade,reject,disabled}}`。
- **Provider 契约**：`resolve_provider(name) -> Provider{responses_url(), chat_url(), configured}`。
- **成本口径**：`request_cost(settings, input, cached, output)`，缓存命中输入单独计价。

## 5. 边界与扩展点
- 流式不做重试；预算按当日累计判定。
- 扩展：新增后端只需在 `GATEWAY_PROVIDERS` 声明；新增信号只需扩展 `score_signals()`。
