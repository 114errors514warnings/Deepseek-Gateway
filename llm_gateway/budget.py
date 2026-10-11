"""预算控制（V2）：每会话 / 每日预算，超预算时降档或拒绝。

设计原则：
- 关闭时（预算为 0）完全不影响路由，零副作用；
- 超预算的处置动作写进日志（budget_decision），可审计；
- 降档映射 max→high→low，绝不升档。
"""
from __future__ import annotations

from dataclasses import dataclass

from .settings import Settings
from .storage import UsageStore

DOWNGRADE = {"max": "high", "high": "low", "low": "low", "none": "none"}


@dataclass(frozen=True)
class BudgetDecision:
    allowed_effort: str
    decision: str  # ok | downgrade | reject | disabled
    reason: str

    def as_dict(self) -> dict:
        return {"allowed_effort": self.allowed_effort, "decision": self.decision, "reason": self.reason}


class BudgetManager:
    def __init__(self, settings: Settings, store: UsageStore):
        self.settings = settings
        self.store = store

    @property
    def enabled(self) -> bool:
        return self.settings.daily_budget_usd > 0 or self.settings.session_budget_usd > 0

    def evaluate(self, session_id: str, effort: str) -> BudgetDecision:
        if not self.enabled:
            return BudgetDecision(effort, "disabled", "budget:disabled")

        over: list[str] = []
        daily = self.settings.daily_budget_usd
        session = self.settings.session_budget_usd
        if daily > 0 and self.store.spend_today() >= daily:
            over.append("daily")
        if session > 0 and self.store.spend_today(session_id) >= session:
            over.append("session")
        if not over:
            return BudgetDecision(effort, "ok", "budget:ok")

        scope = "+".join(over)
        if self.settings.budget_action == "reject":
            return BudgetDecision(effort, "reject", f"budget:exceeded:{scope}")
        downgraded = DOWNGRADE.get(effort, "low")
        return BudgetDecision(downgraded, "downgrade", f"budget:downgrade:{scope}")
