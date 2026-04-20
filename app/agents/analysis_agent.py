from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List

from app.agents.base import BaseAgent
from app.config import Settings
from app.models.agents import ChurnRisk, HealthAnalysis
from app.models.customer import CustomerContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are an expert Customer Success analyst. Given a normalised customer context, produce a
comprehensive health analysis. Respond ONLY with a JSON object matching this schema:

{
  "health_score": <float 0-100>,
  "churn_risk": <"low"|"medium"|"high"|"critical">,
  "risk_factors": [<string>, ...],
  "positive_signals": [<string>, ...],
  "summary": <string, 2-3 sentences>
}

Scoring guidance:
  - usage_rate < 0.4 is a major red flag
  - declining usage_trend adds 20 pts of risk
  - critical open tickets indicate severe dissatisfaction
  - days_to_renewal < 60 + high churn_risk = critical
  - avg_satisfaction_score < 3 is alarming
"""


class AnalysisAgent(BaseAgent):
    """
    Analyses customer health and churn risk.
    Uses OpenAI when available, otherwise falls back to algorithmic scoring.
    """

    name = "AnalysisAgent"

    def __init__(self, config: Settings):
        super().__init__(config)

    def run(self, ctx: CustomerContext) -> HealthAnalysis:
        t0 = self._timer()
        logger.info("[AnalysisAgent] Analysing account %s", ctx.account_id)

        if self._client:
            result = self._run_llm(ctx)
        else:
            result = self._run_mock(ctx)

        elapsed = self._elapsed_ms(t0)
        logger.info(
            "[AnalysisAgent] Done in %dms | health_score=%.1f churn_risk=%s",
            elapsed,
            result.health_score,
            result.churn_risk,
        )
        return result

    # ── LLM path ──────────────────────────────────────────────────────────────

    def _run_llm(self, ctx: CustomerContext) -> HealthAnalysis:
        user_msg = f"Customer context:\n{json.dumps(ctx.model_dump(mode='json'), indent=2)}"
        data = self._call_llm(_SYSTEM_PROMPT, user_msg)
        return HealthAnalysis(
            health_score=float(data["health_score"]),
            churn_risk=ChurnRisk(data["churn_risk"]),
            risk_factors=data["risk_factors"],
            positive_signals=data["positive_signals"],
            summary=data["summary"],
            analyzed_at=datetime.now(timezone.utc),
        )

    # ── Mock / algorithmic path ────────────────────────────────────────────────

    def _run_mock(self, ctx: CustomerContext) -> HealthAnalysis:
        score = 100.0
        risk_factors: List[str] = []
        positive_signals: List[str] = []

        # ── Usage ──────────────────────────────────────────────────────────────
        if ctx.usage_rate < 0.3:
            score -= 30
            risk_factors.append(f"Very low usage rate ({ctx.usage_rate:.0%})")
        elif ctx.usage_rate < 0.5:
            score -= 15
            risk_factors.append(f"Below-average usage rate ({ctx.usage_rate:.0%})")
        elif ctx.usage_rate >= 0.8:
            positive_signals.append(f"High usage rate ({ctx.usage_rate:.0%})")

        trend = ctx.raw_usage.usage_trend
        if trend == "declining":
            score -= 20
            risk_factors.append("Usage trend is declining")
        elif trend == "increasing":
            score += 5
            positive_signals.append("Usage is trending upward")

        # ── Feature adoption ───────────────────────────────────────────────────
        if ctx.feature_adoption_rate < 0.3:
            score -= 15
            risk_factors.append(f"Low feature adoption ({ctx.feature_adoption_rate:.0%})")
        elif ctx.feature_adoption_rate >= 0.7:
            positive_signals.append(f"Strong feature adoption ({ctx.feature_adoption_rate:.0%})")

        # ── Support tickets ────────────────────────────────────────────────────
        if ctx.critical_tickets > 0:
            score -= 20 * ctx.critical_tickets
            risk_factors.append(f"{ctx.critical_tickets} open critical support ticket(s)")
        elif ctx.open_tickets > 3:
            score -= 10
            risk_factors.append(f"{ctx.open_tickets} open support tickets")

        if ctx.avg_satisfaction_score is not None:
            if ctx.avg_satisfaction_score < 3.0:
                score -= 15
                risk_factors.append(
                    f"Low CSAT score ({ctx.avg_satisfaction_score:.1f}/5)"
                )
            elif ctx.avg_satisfaction_score >= 4.5:
                positive_signals.append(
                    f"Excellent CSAT score ({ctx.avg_satisfaction_score:.1f}/5)"
                )

        # ── Renewal proximity ──────────────────────────────────────────────────
        if ctx.days_to_renewal < 30:
            score -= 15
            risk_factors.append(f"Renewal in {ctx.days_to_renewal} days")
        elif ctx.days_to_renewal < 60:
            score -= 5
            risk_factors.append(f"Renewal approaching in {ctx.days_to_renewal} days")
        elif ctx.days_to_renewal > 180:
            positive_signals.append(f"Renewal is {ctx.days_to_renewal} days away")

        # ── Expansion signals are positive ─────────────────────────────────────
        strong_signals = [s for s in ctx.expansion_signals if s.strength == "strong"]
        if strong_signals:
            positive_signals.append(f"{len(strong_signals)} strong expansion signal(s)")

        # ── Last login ─────────────────────────────────────────────────────────
        if ctx.raw_usage.last_login_days_ago > 14:
            score -= 10
            risk_factors.append(
                f"No login in {ctx.raw_usage.last_login_days_ago} days"
            )

        score = max(0.0, min(100.0, score))

        if score >= 75:
            churn_risk = ChurnRisk.LOW
        elif score >= 55:
            churn_risk = ChurnRisk.MEDIUM
        elif score >= 35:
            churn_risk = ChurnRisk.HIGH
        else:
            churn_risk = ChurnRisk.CRITICAL

        summary = self._build_summary(ctx, score, churn_risk, risk_factors, positive_signals)

        return HealthAnalysis(
            health_score=round(score, 1),
            churn_risk=churn_risk,
            risk_factors=risk_factors,
            positive_signals=positive_signals,
            summary=summary,
            analyzed_at=datetime.now(timezone.utc),
        )

    def _build_summary(
        self,
        ctx: CustomerContext,
        score: float,
        risk: ChurnRisk,
        risks: List[str],
        positives: List[str],
    ) -> str:
        parts = [
            f"{ctx.company_name} has a health score of {score:.0f}/100 with {risk.value} churn risk."
        ]
        if risks:
            parts.append(f"Key concerns: {risks[0]}.")
        if positives:
            parts.append(f"Positive note: {positives[0]}.")
        return " ".join(parts)
