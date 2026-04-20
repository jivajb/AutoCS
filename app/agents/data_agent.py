from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from app.agents.base import BaseAgent
from app.config import Settings
from app.models.customer import CustomerContext, RawCustomerData, TicketStatus

logger = logging.getLogger(__name__)


class DataAgent(BaseAgent):
    """
    Ingests raw customer JSON and emits a normalised CustomerContext.
    No LLM required — this is pure deterministic transformation.
    """

    name = "DataAgent"

    def __init__(self, config: Settings):
        super().__init__(config)

    def run(self, raw: RawCustomerData) -> CustomerContext:
        t0 = self._timer()
        logger.info("[DataAgent] Normalising account %s (%s)", raw.account_id, raw.company_name)

        usage_rate = (
            raw.usage.monthly_active_users / raw.usage.total_seats
            if raw.usage.total_seats > 0
            else 0.0
        )
        feature_adoption_rate = (
            raw.usage.features_adopted / raw.usage.total_features
            if raw.usage.total_features > 0
            else 0.0
        )

        open_tickets = sum(
            1 for t in raw.support_tickets if t.status != TicketStatus.CLOSED
        )
        critical_tickets = sum(
            1
            for t in raw.support_tickets
            if t.status != TicketStatus.CLOSED and t.priority.value == "critical"
        )

        closed_with_score = [
            t.satisfaction_score
            for t in raw.support_tickets
            if t.satisfaction_score is not None
        ]
        avg_satisfaction: Optional[float] = (
            sum(closed_with_score) / len(closed_with_score) if closed_with_score else None
        )

        today = date.today()
        days_to_renewal = (raw.renewal.renewal_date - today).days

        ctx = CustomerContext(
            account_id=raw.account_id,
            company_name=raw.company_name,
            industry=raw.industry,
            company_size=raw.company_size,
            plan_tier=raw.plan_tier,
            account_manager=raw.account_manager,
            usage_rate=round(usage_rate, 4),
            feature_adoption_rate=round(feature_adoption_rate, 4),
            open_tickets=open_tickets,
            critical_tickets=critical_tickets,
            avg_satisfaction_score=round(avg_satisfaction, 2) if avg_satisfaction else None,
            days_to_renewal=days_to_renewal,
            contract_value=raw.renewal.contract_value,
            expansion_signals=raw.expansion_signals,
            raw_usage=raw.usage,
            renewal=raw.renewal,
            normalized_at=datetime.now(timezone.utc),
        )

        elapsed = self._elapsed_ms(t0)
        logger.info(
            "[DataAgent] Done in %dms | usage_rate=%.2f feature_adoption=%.2f "
            "open_tickets=%d days_to_renewal=%d",
            elapsed,
            ctx.usage_rate,
            ctx.feature_adoption_rate,
            ctx.open_tickets,
            ctx.days_to_renewal,
        )
        return ctx
