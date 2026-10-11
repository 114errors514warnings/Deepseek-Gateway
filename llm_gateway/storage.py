from __future__ import annotations

import os
import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_id TEXT,
    provider TEXT NOT NULL,
    model TEXT,
    effort TEXT,
    stream INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL,
    success INTEGER NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp);
"""

SCHEMA_V2_FEEDBACK = """
CREATE TABLE IF NOT EXISTS llm_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_id TEXT,
    session_id TEXT,
    tool_ok INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    error_body_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_feedback_timestamp ON llm_feedback(timestamp);
"""

# V1 老库升级到 V2 需要补齐的列
_MIGRATIONS: dict[str, str] = {
    "routed_reason": "TEXT",
    "upstream_model": "TEXT",
    "error_body_hash": "TEXT",
    "session_id": "TEXT",
    "budget_decision": "TEXT",
}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = int(round((len(ordered) - 1) * p))
    idx = max(0, min(idx, len(ordered) - 1))
    return float(ordered[idx])


class UsageStore:
    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.executescript(SCHEMA_V2_FEEDBACK)
            self._migrate(connection)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(llm_usage)")}
        for column, coltype in _MIGRATIONS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE llm_usage ADD COLUMN {column} {coltype}")

    def record(self, row: dict[str, Any]) -> None:
        columns = tuple(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO llm_usage ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )

    def record_feedback(self, row: dict[str, Any]) -> None:
        columns = tuple(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO llm_feedback ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )

    # ---------- 预算 ----------
    def spend_today(self, session_id: str | None = None) -> float:
        with self._connect() as connection:
            if session_id is None:
                row = connection.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE date(timestamp) = date('now')"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage "
                    "WHERE date(timestamp) = date('now') AND session_id = ?",
                    (session_id,),
                ).fetchone()
        return float(row[0] if row else 0.0)

    # ---------- 反馈闭环 ----------
    def recent_failed_sessions(self, window: int = 20) -> set[str]:
        """返回最近 window 条工具反馈中「执行失败」的会话集合。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, tool_ok FROM llm_feedback ORDER BY id DESC LIMIT ?",
                (max(1, int(window)),),
            ).fetchall()
        failed: set[str] = set()
        for session_id, tool_ok in rows:
            if int(tool_ok) == 0 and session_id:
                failed.add(str(session_id))
        return failed

    # ---------- 汇总 ----------
    def metrics(self) -> dict[str, Any]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                """SELECT COUNT(*) AS requests, COALESCE(SUM(success), 0) AS successes,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(cached_tokens), 0) AS cached_tokens, COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
                   COALESCE(SUM(retry_count), 0) AS retries
                   FROM llm_usage"""
            ).fetchone()
            by_effort = connection.execute(
                "SELECT effort, COUNT(*) AS requests, SUM(success) AS successes, SUM(cost_usd) AS cost_usd "
                "FROM llm_usage GROUP BY effort"
            ).fetchall()
            by_model = connection.execute(
                "SELECT model, COUNT(*) AS requests, SUM(success) AS successes, SUM(cost_usd) AS cost_usd "
                "FROM llm_usage GROUP BY model"
            ).fetchall()
            latencies = [float(r[0]) for r in connection.execute("SELECT latency_ms FROM llm_usage")]
            series = connection.execute(
                "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) AS requests, "
                "COALESCE(SUM(cost_usd), 0) AS cost_usd, COALESCE(SUM(success), 0) AS successes "
                "FROM llm_usage GROUP BY bucket ORDER BY bucket"
            ).fetchall()
            errors = connection.execute(
                "SELECT error_type, COUNT(*) AS count FROM llm_usage "
                "WHERE error_type IS NOT NULL AND error_type != '' GROUP BY error_type ORDER BY count DESC LIMIT 10"
            ).fetchall()
            recent = connection.execute(
                "SELECT timestamp, request_id, provider, model, upstream_model, effort, stream, input_tokens, "
                "cached_tokens, output_tokens, cost_usd, latency_ms, success, retry_count, error_type, "
                "error_body_hash, routed_reason, session_id, budget_decision "
                "FROM llm_usage ORDER BY id DESC LIMIT 50"
            ).fetchall()

        values = dict(total)
        requests = int(values.get("requests") or 0)
        values["success_rate"] = (values["successes"] / requests) if requests else None
        values["cache_hit_rate"] = (values["cached_tokens"] / values["input_tokens"]) if values["input_tokens"] else None
        values["retry_rate"] = (values["retries"] / requests) if requests else None
        values["cost_per_request_usd"] = (values["cost_usd"] / requests) if requests else None
        values["p50_latency_ms"] = _percentile(latencies, 0.50)
        values["p95_latency_ms"] = _percentile(latencies, 0.95)
        values["by_effort"] = [dict(item) for item in by_effort]
        values["by_model"] = [dict(item) for item in by_model]
        values["errors"] = [dict(item) for item in errors]
        values["time_series"] = [
            {
                "bucket": item["bucket"],
                "requests": item["requests"],
                "cost_usd": round(float(item["cost_usd"] or 0.0), 6),
                "success_rate": (item["successes"] / item["requests"]) if item["requests"] else None,
            }
            for item in series
        ]
        values["recent"] = [dict(item) for item in recent]
        values["spend_today_usd"] = round(self.spend_today(), 6)
        return values

    def recent_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT timestamp, request_id, provider, model, upstream_model, effort, stream, input_tokens, "
                "cached_tokens, output_tokens, cost_usd, latency_ms, success, retry_count, error_type, "
                "error_body_hash, routed_reason, session_id, budget_decision "
                "FROM llm_usage ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(item) for item in rows]
