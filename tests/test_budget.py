"""预算控制：关闭 / 降档 / 拒绝，且绝不升档。"""
from __future__ import annotations

from llm_gateway.budget import BudgetManager
from llm_gateway.settings import Settings
from llm_gateway.storage import UsageStore

from conftest import usage_row


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        deepseek_api_key="k", deepseek_base_url="https://upstream.test",
        db_path=str(tmp_path / "b.db"), timeout_seconds=5, default_model="deepseek-flash",
        input_cache_hit_per_m=0.006, input_cache_miss_per_m=0.3, output_per_m=1.2,
    )
    base.update(overrides)
    return Settings(**base)


def test_budget_disabled_is_noop(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "b.db"))
    manager = BudgetManager(_settings(tmp_path), store)
    decision = manager.evaluate("sess", "max")
    # 预算全关：预算不改变档位
    assert manager.enabled is False
    assert decision.decision == "disabled"


def test_budget_under_limit_is_ok(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "b.db"))
    store.record(usage_row(cost=0.01, session_id="sess"))
    manager = BudgetManager(_settings(tmp_path, session_budget_usd=1.0), store)
    decision = manager.evaluate("sess", "high")
    assert decision.decision == "ok"
    assert decision.allowed_effort == "high"


def test_budget_exceeded_downgrades(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "b.db"))
    store.record(usage_row(cost=0.5, session_id="sess"))
    manager = BudgetManager(_settings(tmp_path, session_budget_usd=0.1, budget_action="downgrade"), store)
    decision = manager.evaluate("sess", "max")
    assert decision.decision == "downgrade"
    assert decision.allowed_effort == "high"
    assert "session" in decision.reason


def test_budget_exceeded_rejects(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "b.db"))
    store.record(usage_row(cost=0.5, session_id="sess"))
    manager = BudgetManager(_settings(tmp_path, session_budget_usd=0.1, budget_action="reject"), store)
    decision = manager.evaluate("sess", "high")
    assert decision.decision == "reject"


def test_downgrade_never_upgrades(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "b.db"))
    store.record(usage_row(cost=0.5, session_id="sess"))
    manager = BudgetManager(_settings(tmp_path, daily_budget_usd=0.01, budget_action="downgrade"), store)
    assert manager.evaluate("sess", "low").allowed_effort == "low"


def test_daily_budget_is_global(tmp_path) -> None:
    store = UsageStore(str(tmp_path / "b.db"))
    store.record(usage_row(cost=0.5, session_id="other"))
    manager = BudgetManager(_settings(tmp_path, daily_budget_usd=0.1), store)
    # 别人花的钱也计入每日总额
    assert manager.evaluate("fresh-session", "high").decision == "downgrade"
