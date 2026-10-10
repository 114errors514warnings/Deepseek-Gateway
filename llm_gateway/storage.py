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


class UsageStore:
    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record(self, row: dict[str, Any]) -> None:
        columns = tuple(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO llm_usage ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )

    def metrics(self) -> dict[str, Any]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                """SELECT COUNT(*) AS requests, COALESCE(SUM(success), 0) AS successes,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(cached_tokens), 0) AS cached_tokens, COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                   FROM llm_usage"""
            ).fetchone()
            by_effort = connection.execute(
                "SELECT effort, COUNT(*) AS requests, SUM(success) AS successes, SUM(cost_usd) AS cost_usd FROM llm_usage GROUP BY effort"
            ).fetchall()
        values = dict(total)
        values["success_rate"] = values["successes"] / values["requests"] if values["requests"] else None
        values["cache_hit_rate"] = values["cached_tokens"] / values["input_tokens"] if values["input_tokens"] else None
        values["by_effort"] = [dict(item) for item in by_effort]
        return values

