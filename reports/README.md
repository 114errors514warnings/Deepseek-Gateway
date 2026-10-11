# ContextOptimizer · 报告索引

> 约定：每个已完成阶段产出**两份独立文档** —— `report.md`（阶段报告：交付/测试/门禁/结论）与
> `architecture.md`（架构说明：组件/数据流/契约/边界）。整机架构图由本地 Archify 生成。

## 项目级
- `STAGE_REPORT.md` —— 全项目阶段完成总报告（迭代清单 + 验收证据汇总）

## 阶段文档
| 阶段 | 报告 | 架构说明 |
|---|---|---|
| V1.1 可观测加固 | [report](stages/V1.1/report.md) | [architecture](stages/V1.1/architecture.md) |
| V2 智能路由 + 预算 + 反馈闭环 | [report](stages/V2/report.md) | [architecture](stages/V2/architecture.md) |

## 验收产物
- `acceptance_*.md` / `acceptance_*.json` —— `scripts/run_acceptance.py` 的机器可读 + 可贴报告
- 门禁：L1 pytest、L2 路由准确率/成本节省率/误路由率/计量完整性、L3 真实 A/B（`NO_GATE`）

仅保留三份**口径互不相同**的验收报告（其余为同内容重复运行，已去重）：

| 报告 | 阶段 | 门禁数 | 说明 |
|---|---|---|---|
| [`acceptance_20261011-014317`](acceptance_20261011-014317.md) | 早期（3 门禁） | 3 | pytest + 路由准确率 + L3 |
| [`acceptance_20261011-015953`](acceptance_20261011-015953.md) | 中期（6 门禁，ContextOptimizer 标题） | 6 | 加入基线/成本/误路由/计量门禁 |
| [`acceptance_20261011-213351`](acceptance_20261011-213351.md) | 最终（6 门禁，DeepSeekGateway 标题） | 6 | 当前权威报告，`STAGE_REPORT.md` 引用此份 |

## 架构图（Archify）
- `..\.archify\architecture-gateway-v2-20261011-031500\gateway-v2-architecture.html`
  （Archify `finalize` 全部闸门通过：validate / deliver / check / browser-check）
