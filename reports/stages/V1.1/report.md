# ContextOptimizer · 阶段报告 · V1.1 可观测加固

- 阶段：V1.1
- 状态：✅ 已完成（全量测试通过）
- 日期：2026-10-11
- 关联架构说明：`reports/stages/V1.1/architecture.md`

## 1. 目标（本阶段要回答的问题）
在不动 V1 行为边界（不压缩、不重写上下文）的前提下，让网关"看得见"：每个响应可追溯档位与重试、
每条计量可审计、每个失败可回溯、上游健康可探测。

## 2. 交付物与改动

| 项 | 内容 | 涉及文件 |
|---|---|---|
| 响应头 | 所有响应路径（成功 / 上游 4xx / 5xx / 流式 / 预算拒绝）补齐 `X-Gateway-Effort`、`X-Gateway-Retry-Count`、`X-Gateway-Request-Id`、`X-Gateway-Provider`、`X-Gateway-Upstream-Model`、`X-Gateway-Budget` | `llm_gateway/app.py` |
| 计量列 | 新增 `routed_reason`、`upstream_model`、`error_body_hash`、`session_id`、`budget_decision`；**老库自动迁移**（`ALTER TABLE ADD COLUMN`） | `llm_gateway/storage.py` |
| 指标 | `/metrics` 增加 `p50/p95_latency_ms`、`cost_per_request_usd`、`retry_rate`、`by_model`、`errors` Top-N、`time_series`、`recent`、`spend_today_usd` | `llm_gateway/storage.py` |
| 健康 | `/health` 增加上游连通性探测（`upstream_reachable` / `upstream_status` / `upstream_latency_ms`） | `llm_gateway/app.py` |
| 用量查询 | 新增 `GET /v1/usage?limit=N` | `llm_gateway/app.py` |

## 3. 测试方法（三层）

- L1（pytest，离线、确定性）：`tests/test_gateway.py` 断言响应头、错误透传、流式、`/health` 探测；
  `tests/test_storage.py` 断言 v1 库迁移到 v2、分位数、时间序列、预算花费、反馈闭包。
- L2（阶段门禁）：`python scripts/run_acceptance.py`（本阶段并入整体验收）。
- L3：真实上游冒烟（需 `DEEPSEEK_API_KEY`），标 `NO_GATE`。

## 4. 结果

| 检查 | 结果 |
|---|---|
| 全量 pytest | 通过（V1.1 阶段首次落地时 31 项，后期随 V2 扩展到 40 项） |
| 老库迁移 | `test_v1_db_is_migrated_to_v2` PASS |
| 分位数 / 时间序列 | `test_metrics_percentiles_and_series` PASS |
| 健康探测（可达 / 不可达） | PASS |
| L2 验收 | PASS |

## 5. 边界与不做
- 流式一旦开始 SSE 不可安全重试（`X-Gateway-Retry-Count` 恒为 0）。
- `/health` 用 `/models` 探测，4xx 视为"网络可达"，仅 5xx/异常判不可达。
- 本阶段不引入路由策略变化、不引入预算（属 V2）。

## 6. 演进映射（后续会改哪些文件）
- `routing.py`：V2 改为多信号打分（V1.1 仍用规则路由）。
- `app.py`：V2 抽出 `_proxy` 统一编排，接入预算与反馈闭环。
- `storage.py`：V2 增加反馈表与预算聚合查询。
