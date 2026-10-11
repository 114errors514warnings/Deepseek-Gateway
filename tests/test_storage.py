"""存储层：schema 迁移、分位数、时间序列、预算花费、反馈闭环。"""
from __future__ import annotations

import sqlite3

from llm_gateway.storage import UsageStore

from conftest import usage_row

V1_SCHEMA = """
CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_id TEXT, provider TEXT NOT NULL, model TEXT, effort TEXT,
    stream INTEGER NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL,
    success INTEGER NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0, error_type TEXT
);
"""


def test_v1_db_is_migrated_to_v2(tmp_path) -> None:
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(V1_SCHEMA)
    store = UsageStore(str(db))
    with store._connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(llm_usage)")}
    for column in ("routed_reason", "upstream_model", "error_body_hash", "session_id", "budget_decision"):
        assert column in columns
    # 迁移后仍可正常写入/读取
    store.record(usage_row(cost=0.1))
    assert store.metrics()["requests"] == 1


def test_metrics_percentiles_and_series(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "m.db"))
    for latency in (10, 20, 30, 40, 50):
        store.record(usage_row(cost=0.1, latency=latency))
    metrics = store.metrics()
    assert metrics["requests"] == 5
    assert metrics["p50_latency_ms"] == 30
    assert metrics["p95_latency_ms"] == 50
    assert metrics["cost_per_request_usd"] == 0.1
    assert metrics["by_effort"] and metrics["by_effort"][0]["effort"] == "high"
    assert metrics["time_series"] and metrics["time_series"][0]["requests"] == 5
    assert len(metrics["recent"]) == 5


def test_spend_today_scoped_by_session(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "s.db"))
    store.record(usage_row(cost=0.5, session_id="a"))
    store.record(usage_row(cost=0.25, session_id="b"))
    assert round(store.spend_today(), 4) == 0.75
    assert round(store.spend_today("a"), 4) == 0.5
    assert round(store.spend_today("b"), 4) == 0.25


def test_feedback_marks_failed_sessions(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "f.db"))
    store.record_feedback({"request_id": "r1", "session_id": "sess-ok", "tool_ok": 1})
    store.record_feedback({"request_id": "r2", "session_id": "sess-bad", "tool_ok": 0})
    failed = store.recent_failed_sessions(window=10)
    assert "sess-bad" in failed
    assert "sess-ok" not in failed


def test_metrics_empty_is_safe(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "e.db"))
    metrics = store.metrics()
    assert metrics["requests"] == 0
    assert metrics["success_rate"] is None
    assert metrics["p95_latency_ms"] is None
