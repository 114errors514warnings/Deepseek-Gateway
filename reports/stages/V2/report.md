# ContextOptimizer · 阶段报告 · V2 智能路由 + 预算 + 反馈闭环

- 阶段：V2
- 状态：✅ 已完成（全量测试 + L2 验收全 PASS）
- 日期：2026-10-11
- 关联架构说明：`reports/stages/V2/architecture.md`

## 1. 目标
把网关从"被动代理 + 固定规则"升级为"可解释的路由 + 可核算的预算 + 可闭环的失败升级 + 可扩展的多后端 + 可视化的运维面板"。

## 2. 交付物与改动

| 子项 | 内容 | 文件 |
|---|---|---|
| 智能路由 | 多信号打分（长度 / 工具数 / 轮次 / 关键词 / 历史失败）→ `effort` + 可审计 `routed_reason` | `routing.py` |
| 预算 | 每会话 / 每日预算；超预算降档（`max→high→low`，绝不升档）或拒绝（429）；决策入日志 | `budget.py`、`storage.py` |
| 反馈闭环 | `POST /v1/feedback` 接入上游工具结果 → 失败会话后续请求升一档 | `app.py`、`storage.py` |
| 多 provider | Provider 抽象；`X-Gateway-Provider` 选择任意 OpenAI 兼容后端 | `providers.py`、`settings.py` |
| Dashboard | `GET /dashboard` 单页可视化（成本/成功率/分位/错误 Top-N） | `dashboard.py` |
| 计量抽出 | 线上/离线同口径 | `metering.py` |
| 离线评测 | 路由准确率 / 成本 A/B / 误路由率 / 计量完整性 | `evaluation.py`、`eval/routing_cases.jsonl` |
| CI 门禁 | 一键验收 + 冻结基线 | `scripts/{run_acceptance.py,run_gates.ps1,freeze_baseline.py}`、`baselines/routing_accuracy.json` |

## 3. 测试方法（三层）

- L1（pytest）：`test_routing.py`（准确率门禁）、`test_budget.py`、`test_storage.py`、`test_metering.py`、
  `test_evaluation.py`、`test_gateway.py`。
- L2（门禁，`run_acceptance.py`）：
  | 门禁 | 目标 | 实测 | 判定 |
  |---|---|---|---|
  | routing_accuracy | ≥ 0.85 | 1.0000 | PASS |
  | routing_no_regression（冻结基线） | Δ ≥ -0.02 | +0.0000 | PASS |
  | cost_saving_rate（模型化） | ≥ 0.15 | 0.4069 | PASS |
  | false_route_rate | ≤ 0.02 | 0.0000 | PASS |
  | metering_completeness | ≥ 0.99 | 1.0000 | PASS |
- L3：真实 A/B（成本/成功率/延迟）需真实上游 → `NO_GATE`。

## 4. 结果
- 全量 pytest：**40 passed**，退出码 0。
- `run_acceptance.py`：结论 **PASS**，退出码 0。
- 测试轮次：路由/预算/反馈各迭代均在 1 次内通过；无 3 次失败项。

## 5. 口径与假设留痕
- 成本节省率是**离线模型化**：基线假设复杂任务在 high 下失败并升级 max 重试一次；输出倍率 low=0.6/high=1.0/max=1.6。真实 A/B 属 L3。
- 数据集固定：`eval/routing_cases.jsonl`（sha256 `94dd3a0f…cafe5`，30 条）；换集须新建基线。
- 指标口径集中在 `metering.py` 与 `evaluation.py`；改公式须 bump 版本。

## 6. 边界与不做
- 路由是离线可解释的规则打分，非学习模型（升级分类器属 Phase 4，按证据触发）。
- 流式不重试；预算以"当日累计"为准（不预测当次成本）。
- 多 provider 密钥仅存本地库/环境，不写日志、不回显。
