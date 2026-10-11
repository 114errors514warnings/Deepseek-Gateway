"""DeepSeekGateway 验收脚本（离线）。

产出：
- reports/acceptance_<timestamp>.json（机器可读，含每项门禁与 per-case）
- reports/acceptance_<timestamp>.md（可直接贴报告）

退出码：0 = 全 PASS/NO_GATE/BASELINE；1 = 存在 FAIL（可直接用作 CI 门禁）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_gateway.evaluation import (  # noqa: E402
    USAGE_SAMPLES, evaluate_cost_ab, load_workload, metering_completeness,
)
from llm_gateway.routing import selected_route  # noqa: E402
from llm_gateway.settings import load_settings  # noqa: E402

CASES_PATH = ROOT / "eval" / "routing_cases.jsonl"
BASELINE_PATH = ROOT / "baselines" / "routing_accuracy.json"
REPORT_DIR = ROOT / "reports"

ROUTING_ACCURACY_GATE = 0.85
ROUTING_ACCURACY_TOLERANCE = 0.02
COST_SAVING_GATE = 0.15
FALSE_ROUTE_GATE = 0.02
METERING_GATE = 0.99


def load_cases() -> list[dict]:
    return [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_routing() -> dict:
    cases = load_cases()
    per_case = []
    hits = 0
    for case in cases:
        payload = {k: v for k, v in case.items() if k in ("input", "messages", "tools")}
        decision = selected_route(payload)
        ok = decision.effort == case["expected"]
        hits += int(ok)
        per_case.append({
            "id": case["id"], "category": case["category"], "expected": case["expected"],
            "got": decision.effort, "reason": decision.reason, "pass": ok,
        })
    accuracy = hits / len(cases) if cases else 0.0
    return {"accuracy": accuracy, "hits": hits, "total": len(cases), "per_case": per_case}


def compare_with_baseline(accuracy: float) -> tuple[str, str]:
    if not BASELINE_PATH.exists():
        return "BASELINE", "缺少冻结基线文件"
    frozen = json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("accuracy")
    if frozen is None:
        return "BASELINE", "基线文件无 accuracy 字段"
    delta = accuracy - float(frozen)
    status = "PASS" if delta >= -ROUTING_ACCURACY_TOLERANCE else "FAIL"
    return status, f"冻结基线={frozen}，Δ={delta:+.4f}（容忍 {ROUTING_ACCURACY_TOLERANCE}）"


def run_pytest() -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(ROOT),
                          capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    return proc.returncode, " ".join(tail)


def build_report() -> tuple[dict, int]:
    gates: list[dict] = []

    rc, tail = run_pytest()
    gates.append({
        "layer": "L1", "id": "pytest_suite", "desc": "功能/单元测试全部通过",
        "value": rc, "target": "exit == 0", "status": "PASS" if rc == 0 else "FAIL", "detail": tail,
    })

    routing = evaluate_routing()
    gates.append({
        "layer": "L2", "id": "routing_accuracy", "desc": "离线回放带标签请求的路由准确率",
        "value": round(routing["accuracy"], 4), "target": f">= {ROUTING_ACCURACY_GATE}",
        "status": "PASS" if routing["accuracy"] >= ROUTING_ACCURACY_GATE else "FAIL",
        "detail": f"{routing['hits']}/{routing['total']} 命中",
    })

    baseline_status, baseline_detail = compare_with_baseline(routing["accuracy"])
    gates.append({
        "layer": "L2", "id": "routing_no_regression", "desc": "相对冻结基线不回归（基线只读）",
        "value": round(routing["accuracy"], 4), "target": f"Δ >= -{ROUTING_ACCURACY_TOLERANCE}",
        "status": baseline_status, "detail": baseline_detail,
    })

    settings = load_settings()
    workload = load_workload(CASES_PATH)
    ab = evaluate_cost_ab(settings, workload)
    gates.append({
        "layer": "L2", "id": "cost_saving_rate", "desc": "相对「全 high」基线的模型化成本节省率",
        "value": round(ab["cost_saving_rate"], 4), "target": f">= {COST_SAVING_GATE}",
        "status": "PASS" if ab["cost_saving_rate"] >= COST_SAVING_GATE else "FAIL",
        "detail": f"baseline=${ab['baseline_cost_usd']} smart=${ab['smart_cost_usd']}（离线模型化，真实 A/B 属 L3）",
    })
    gates.append({
        "layer": "L2", "id": "false_route_rate", "desc": "应 max 却路由 low 的比例",
        "value": round(ab["false_route_rate"], 4), "target": f"<= {FALSE_ROUTE_GATE}",
        "status": "PASS" if ab["false_route_rate"] <= FALSE_ROUTE_GATE else "FAIL",
        "detail": f"误路由 {len(ab['misroutes'])} 例 {ab['misroutes']}",
    })

    completeness = metering_completeness(USAGE_SAMPLES)
    gates.append({
        "layer": "L2", "id": "metering_completeness", "desc": "usage 解析成功率",
        "value": round(completeness, 4), "target": f">= {METERING_GATE}",
        "status": "PASS" if completeness >= METERING_GATE else "FAIL",
        "detail": f"{len(USAGE_SAMPLES)} 种 usage 结构",
    })

    gates.append({
        "layer": "L3", "id": "deploy_smoke", "desc": "经真实客户端调用上游的部署冒烟 + 真实 A/B（成本/成功率/延迟）",
        "value": None, "target": "success_rate 不降、p95 ≤ +20%、cost ↓",
        "status": "NO_GATE", "detail": "需要真实 DEEPSEEK_API_KEY 与网络，离线环境不判定（不参与退出码）",
    })

    failed = [g for g in gates if g["status"] == "FAIL"]
    report = {
        "project": "DeepSeekGateway",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gates": gates,
        "routing": routing,
        "cost_ab": ab,
        "conclusion": "FAIL" if failed else "PASS",
    }
    return report, (1 if failed else 0)


def render_markdown(report: dict) -> str:
    lines = [
        "# DeepSeekGateway 验收报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 结论：**{report['conclusion']}**",
        "",
        "| 层 | 门禁 | 指标 | 实测 | 目标 | 判定 |",
        "|---|---|---|---|---|---|",
    ]
    for gate in report["gates"]:
        value = "—" if gate["value"] is None else gate["value"]
        lines.append(f"| {gate['layer']} | {gate['id']} | {gate['desc']} | {value} | {gate['target']} | {gate['status']} |")
    lines += ["", "## 路由逐例结果", "",
              "| id | 类别 | 期望 | 实际 | 判定 | 路由原因 |", "|---|---|---|---|---|---|"]
    for case in report["routing"]["per_case"]:
        lines.append(f"| {case['id']} | {case['category']} | {case['expected']} | {case['got']} | "
                     f"{'✅' if case['pass'] else '❌'} | {case['reason']} |")
    lines += ["", "## 成本 A/B（离线模型化）", "",
              f"- 全 high 基线：${report['cost_ab']['baseline_cost_usd']}",
              f"- 智能路由：${report['cost_ab']['smart_cost_usd']}",
              f"- 成本节省率：{report['cost_ab']['cost_saving_rate']:.2%}",
              f"- 误路由率：{report['cost_ab']['false_route_rate']:.2%}", ""]
    detail = [g for g in report["gates"] if g.get("detail")]
    if detail:
        lines += ["## 明细", ""]
        for gate in detail:
            lines.append(f"- **{gate['id']}**：{gate['detail']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    report, code = build_report()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = REPORT_DIR / f"acceptance_{stamp}.json"
    md_path = REPORT_DIR / f"acceptance_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    md_path.write_text(markdown, encoding="utf-8")

    print(markdown)
    print(f"[report] {json_path}")
    print(f"[report] {md_path}")
    print(f"[exit] {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
