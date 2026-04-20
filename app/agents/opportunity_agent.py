from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List

from app.agents.base import BaseAgent
from app.config import Settings
from app.models.agents import (
    HealthAnalysis,
    Opportunity,
    OpportunityAnalysis,
    OpportunityType,
)
from app.models.customer import CustomerContext, PlanTier

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are a Customer Success revenue specialist. Analyse the customer context and health data to
identify upsell and expansion opportunities. Respond ONLY with a JSON object:

{
  "opportunities": [
    {
      "type": <"seat_expansion"|"tier_upgrade"|"cross_sell"|"multi_year"|"referral">,
      "description": <string>,
      "estimated_value": <float, incremental ARR in USD>,
      "confidence": <float 0-1>,
      "rationale": <string>
    }
  ],
  "total_expansion_potential": <float>,
  "priority": <"low"|"medium"|"high">
}

Only include opportunities with confidence >= 0.3. Return an empty list if none exist.
"""


class OpportunityAgent(BaseAgent):
    """
    Identifies upsell and expansion opportunities from customer context + health analysis.
    """

    name = "OpportunityAgent"

    def __init__(self, config: Settings):
        super().__init__(config)

    def run(self, ctx: CustomerContext, health: HealthAnalysis) -> OpportunityAnalysis:
        t0 = self._timer()
        logger.info("[OpportunityAgent] Scanning account %s", ctx.account_id)

        if self._client:
            result = self._run_llm(ctx, health)
        else:
            result = self._run_mock(ctx, health)

        elapsed = self._elapsed_ms(t0)
        logger.info(
            "[OpportunityAgent] Done in %dms | %d opportunities | potential=$%.0f | priority=%s",
            elapsed,
            len(result.opportunities),
            result.total_expansion_potential,
            result.priority,
        )
        return result

    # ── LLM path ──────────────────────────────────────────────────────────────

    def _run_llm(self, ctx: CustomerContext, health: HealthAnalysis) -> OpportunityAnalysis:
        payload = {
            "customer_context": ctx.model_dump(mode="json"),
            "health_analysis": health.model_dump(mode="json"),
        }
        user_msg = json.dumps(payload, indent=2)
        data = self._call_llm(_SYSTEM_PROMPT, user_msg)
        opps = [
            Opportunity(
                type=OpportunityType(o["type"]),
                description=o["description"],
                estimated_value=float(o["estimated_value"]),
                confidence=float(o["confidence"]),
                rationale=o["rationale"],
            )
            for o in data.get("opportunities", [])
        ]
        return OpportunityAnalysis(
            opportunities=opps,
            total_expansion_potential=float(data.get("total_expansion_potential", 0)),
            priority=data.get("priority", "low"),
            analyzed_at=datetime.now(timezone.utc),
        )

    # ── Mock / algorithmic path ────────────────────────────────────────────────

    def _run_mock(self, ctx: CustomerContext, health: HealthAnalysis) -> OpportunityAnalysis:
        opportunities: List[Opportunity] = []
        acv = ctx.contract_value

        # Only surface opportunities if the account isn't critical/high churn risk
        if health.churn_risk.value in ("critical", "high"):
            # Don't push expansion to a churning account
            return OpportunityAnalysis(
                opportunities=[],
                total_expansion_potential=0.0,
                priority="low",
                analyzed_at=datetime.now(timezone.utc),
            )

        # Seat expansion — usage crowding
        if ctx.usage_rate >= 0.85:
            est = acv * 0.25
            opportunities.append(
                Opportunity(
                    type=OpportunityType.SEAT_EXPANSION,
                    description=f"Seat utilisation is at {ctx.usage_rate:.0%} — additional seats likely needed",
                    estimated_value=round(est, 0),
                    confidence=0.80,
                    rationale="Near-capacity seat usage is a reliable expansion signal",
                )
            )

        # Tier upgrade — high feature adoption on a lower tier
        if ctx.plan_tier != PlanTier.ENTERPRISE and ctx.feature_adoption_rate >= 0.75:
            est = acv * 0.40
            opportunities.append(
                Opportunity(
                    type=OpportunityType.TIER_UPGRADE,
                    description=(
                        f"Adopted {ctx.feature_adoption_rate:.0%} of features on {ctx.plan_tier.value} plan; "
                        "ready for next tier"
                    ),
                    estimated_value=round(est, 0),
                    confidence=0.65,
                    rationale="High feature adoption indicates the customer has outgrown their current plan",
                )
            )

        # Multi-year — healthy account with renewal < 120 days
        if health.health_score >= 70 and 30 <= ctx.days_to_renewal <= 120:
            est = acv * 0.10  # discount in lieu of commitment
            opportunities.append(
                Opportunity(
                    type=OpportunityType.MULTI_YEAR,
                    description="Healthy account approaching renewal — ideal time for multi-year commit",
                    estimated_value=round(est, 0),
                    confidence=0.55,
                    rationale="Multi-year deals reduce churn risk and increase LTV",
                )
            )

        # Explicit expansion signals from raw data
        for sig in ctx.expansion_signals:
            if sig.signal_type == "new_department" and sig.strength in ("moderate", "strong"):
                est = acv * 0.30
                opportunities.append(
                    Opportunity(
                        type=OpportunityType.CROSS_SELL,
                        description=sig.description,
                        estimated_value=round(est, 0),
                        confidence=0.60 if sig.strength == "strong" else 0.40,
                        rationale=f"Expansion signal: {sig.signal_type} ({sig.strength})",
                    )
                )
            elif sig.signal_type == "referral" and sig.strength == "strong":
                opportunities.append(
                    Opportunity(
                        type=OpportunityType.REFERRAL,
                        description=sig.description,
                        estimated_value=0.0,
                        confidence=0.70,
                        rationale="Happy customer with strong referral intent",
                    )
                )

        total = sum(o.estimated_value for o in opportunities)
        priority = "low"
        if total > acv * 0.5:
            priority = "high"
        elif total > acv * 0.2:
            priority = "medium"

        return OpportunityAnalysis(
            opportunities=opportunities,
            total_expansion_potential=round(total, 0),
            priority=priority,
            analyzed_at=datetime.now(timezone.utc),
        )
