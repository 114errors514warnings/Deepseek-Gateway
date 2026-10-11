"""冻结新基线（基线只读原则：不覆盖旧文件，只新建带时间戳的文件）。

用法：
    python scripts/freeze_baseline.py            # 生成 baselines/routing_accuracy.json（首次）
    python scripts/freeze_baseline.py --force    # 已存在则另存为 routing_accuracy_<时间戳>.json
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_gateway.routing import selected_route  # noqa: E402

CASES_PATH = ROOT / "eval" / "routing_cases.jsonl"
BASELINE_DIR = ROOT / "baselines"


def measure() -> dict:
    raw = CASES_PATH.read_bytes()
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    hits = 0
    for case in cases:
        payload = {k: v for k, v in case.items() if k in ("input", "messages", "tools")}
        hits += int(selected_route(payload).effort == case["expected"])
    return {
        "project": "DeepSeekGateway",
        "metric": "routing_accuracy",
        "accuracy": hits / len(cases) if cases else 0.0,
        "hits": hits,
        "total": len(cases),
        "dataset": "eval/routing_cases.jsonl",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "note": "冻结基线：只读，不重跑。换口径/换数据集必须新建文件并 bump。",
        "frozen_at": time.strftime("%Y-%m-%d"),
    }


def main() -> int:
    force = "--force" in sys.argv
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    target = BASELINE_DIR / "routing_accuracy.json"
    if target.exists() and force:
        target = BASELINE_DIR / f"routing_accuracy_{time.strftime('%Y%m%d-%H%M%S')}.json"
        print(f"[freeze] 既有基线保留，新建 {target.name}")
    elif target.exists():
        print(f"[freeze] {target.name} 已存在（只读）。如需新基线请加 --force。")
        return 0
    data = measure()
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[freeze] 写入 {target}  accuracy={data['accuracy']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
