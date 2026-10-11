# ContextOptimizer · 架构说明 · V1.1 可观测加固

> 配套报告：`reports/stages/V1.1/report.md`；整机架构图：`.archify/architecture-gateway-v2-20261011-031500/gateway-v2-architecture.html`

## 1. 组件与职责

```
客户端(Codex/Cline) ──HTTP──▶ Gateway(FastAPI :9000)
                                   │
                     ┌─────────────┼───────────────┐
                     ▼             ▼               ▼
              转发上游(responses/chat)   计量落库(SQLite)   运维接口(/health /metrics)
```

| 组件 | 职责 | 文件 |
|---|---|---|
| Gateway 路由层 | 解析请求、选档位、转发、重试升级、记录 | `llm_gateway/app.py` |
| 计量层 | usage 解析 + 成本核算（V1.1 内联，V2 抽出） | `llm_gateway/app.py` → V2 `metering.py` |
| 存储层 | usage 表 + 迁移 + 聚合 | `llm_gateway/storage.py` |
| 配置层 | 环境变量 → 运行期配置 | `llm_gateway/settings.py` |

## 2. 请求数据流（V1.1）
1. 客户端 POST `/v1/responses` 或 `/v1/chat/completions`。
2. 校验 API Key → 解析 JSON → 计算 `effort`（V1 规则版）。
3. 转发上游；非流式失败可从 `low/high` 升级 `max` 重试一次。
4. 无论成功/失败，落一条 `llm_usage`，附 `routed_reason` 等审计列。
5. 返回体带 `X-Gateway-*` 头。

## 3. 关键契约
- **响应头契约**：任何响应都带 `X-Gateway-Effort` 与 `X-Gateway-Retry-Count`；成功/上游错误另带 `Provider/Upstream-Model/Budget`。
- **计量口径**：`usage_tokens()` 兼容 Responses（`input_tokens/output_tokens`）与 Chat（`prompt_tokens/completion_tokens`）及缓存字段；成本按 `USD/百万 token` 计算。
- **库模式**：`llm_usage` 为唯一事实表；新增列通过 `PRAGMA table_info` 探测 + `ALTER TABLE` 幂等迁移。

## 4. 边界
- 不保存请求体正文（隐私），只存摘要与哈希。
- 健康探测有独立短超时（`GATEWAY_HEALTH_PROBE_TIMEOUT`），不阻塞主链路。
