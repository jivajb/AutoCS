from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.agents.base import BaseAgent
from app.config import Settings
from app.models.agents import (
    ActionType,
    ChurnRisk,
    Decision,
    HealthAnalysis,
    OpportunityAnalysis,
)
from app.models.customer import CustomerContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are a Customer Success decision engine. Given the full customer context, health analysis,
and expansion opportunities, decide the best next action.

Respond ONLY with a JSON object:
{
  "primary_action": <"send_email"|"create_task"|"alert_human"|"update_crm"|"no_action">,
  "secondary_actions": [<same enum values>],
  "confidence": <float 0-1>,
  "rationale": <string>,
  "requires_approval": <bool>,
  "urgency": <"low"|"medium"|"high"|"immediate">,
  "action_data": {
    "email_subject": <string or null>,
    "email_body_summary": <string or null>,
    "task_title": <string or null>,
    "task_description": <string or null>,
    "alert_message": <string or null>,
    "crm_fields": {<field>: <value>} or null
  }
}

Rules:
- churn_risk=critical → alert_human (immediate) + requires_approval=true
- churn_risk=high → send_email + create_task, confidence based on evidence
- good health + opportunities → send_email (expansion pitch)
- low risk, no opportunities → update_crm or no_action
- confidence < 0.7 → requires_approval=true
"""


class DecisionAgent(BaseAgent):
    """
    Synthesises all upstream agent outputs into a concrete, actionable decision.
    """

    name = "DecisionAgent"

    def __init__(self, config: Settings):
        super().__init__(config)

    def run(
        self,
        ctx: CustomerContext,
        health: HealthAnalysis,
        opportunities: OpportunityAnalysis,
    ) -> Decision:
        t0 = self._timer()
        logger.info("[DecisionAgent] Deciding for account %s", ctx.account_id)

        if self._client:
            result = self._run_llm(ctx, health, opportunities)
        else:
            result = self._run_mock(ctx, health, opportunities)

        # Enforce HITL threshold regardless of agent output
        if result.confidence < self.config.hitl_confidence_threshold:
            result = result.model_copy(update={"requires_approval": True})

        elapsed = self._elapsed_ms(t0)
        logger.info(
            "[DecisionAgent] Done in %dms | action=%s confidence=%.2f requires_approval=%s",
            elapsed,
            result.primary_action,
            result.confidence,
            result.requires_approval,
        )
        return result

    # ── LLM path ──────────────────────────────────────────────────────────────

    def _run_llm(
        self,
        ctx: CustomerContext,
        health: HealthAnalysis,
        opportunities: OpportunityAnalysis,
    ) -> Decision:
        payload = {
            "customer_context": ctx.model_dump(mode="json"),
            "health_analysis": health.model_dump(mode="json"),
            "opportunities": opportunities.model_dump(mode="json"),
        }
        data = self._call_llm(_SYSTEM_PROMPT, json.dumps(payload, indent=2))
        return Decision(
            primary_action=ActionType(data["primary_action"]),
            secondary_actions=[ActionType(a) for a in data.get("secondary_actions", [])],
            confidence=float(data["confidence"]),
            rationale=data["rationale"],
            requires_approval=bool(data["requires_approval"]),
            urgency=data["urgency"],
            action_data=data.get("action_data", {}),
            decided_at=datetime.now(timezone.utc),
        )

    # ── Mock / rule-based path ─────────────────────────────────────────────────

    def _run_mock(
        self,
        ctx: CustomerContext,
        health: HealthAnalysis,
        opportunities: OpportunityAnalysis,
    ) -> Decision:
        risk = health.churn_risk
        score = health.health_score
        has_opps = len(opportunities.opportunities) > 0
        renewal_soon = ctx.days_to_renewal < 60

        primary: ActionType
        secondary: List[ActionType] = []
        confidence: float
        urgency: str
        rationale: str
        requires_approval: bool
        action_data: Dict[str, Any] = {}

        if risk == ChurnRisk.CRITICAL:
            primary = ActionType.ALERT_HUMAN
            secondary = [ActionType.CREATE_TASK, ActionType.SEND_EMAIL]
            confidence = 0.95
            urgency = "immediate"
            requires_approval = True
            rationale = (
                f"{ctx.company_name} is at critical churn risk (score {score:.0f}). "
                "Immediate human intervention required."
            )
            action_data = {
                "alert_message": (
                    f"CRITICAL: {ctx.company_name} has health score {score:.0f} and "
                    f"renewal in {ctx.days_to_renewal} days. Risk factors: "
                    + "; ".join(health.risk_factors[:3])
                ),
                "task_title": f"Urgent: Retention intervention for {ctx.company_name}",
                "task_description": (
                    f"Account {ctx.account_id} is at critical churn risk. "
                    f"Contract value: ${ctx.contract_value:,.0f}. "
                    f"Top risk factors: {', '.join(health.risk_factors[:2])}."
                ),
                "email_subject": f"Checking in on your experience with us — {ctx.company_name}",
                "email_body_summary": (
                    "Express concern, offer executive sponsor call, "
                    "outline commitment to resolving open issues."
                ),
            }

        elif risk == ChurnRisk.HIGH:
            primary = ActionType.SEND_EMAIL
            secondary = [ActionType.CREATE_TASK, ActionType.UPDATE_CRM]
            confidence = 0.75
            urgency = "high"
            requires_approval = False
            rationale = (
                f"{ctx.company_name} shows high churn risk (score {score:.0f}). "
                "A proactive outreach email + follow-up task is recommended."
            )
            action_data = {
                "email_subject": f"Let's talk about maximising your success — {ctx.company_name}",
                "email_body_summary": (
                    "Acknowledge recent issues, offer a success review call, "
                    "share relevant product updates."
                ),
                "task_title": f"Schedule success review call with {ctx.company_name}",
                "task_description": (
                    f"Follow up within 48 hours. Risk factors: "
                    + ", ".join(health.risk_factors[:2])
                ),
                "crm_fields": {
                    "health_score": round(score, 1),
                    "churn_risk": risk.value,
                    "last_csm_action": "outreach_email",
                },
            }

        elif has_opps and score >= 65:
            primary = ActionType.SEND_EMAIL
            secondary = [ActionType.UPDATE_CRM]
            top_opp = opportunities.opportunities[0]
            confidence = min(0.85, top_opp.confidence + 0.1)
            urgency = "high" if renewal_soon else "medium"
            requires_approval = False
            rationale = (
                f"{ctx.company_name} is healthy (score {score:.0f}) with "
                f"${opportunities.total_expansion_potential:,.0f} expansion potential. "
                "Time to grow the account."
            )
            action_data = {
                "email_subject": f"Expanding your success with {ctx.company_name}",
                "email_body_summary": (
                    f"Highlight {top_opp.type.value.replace('_', ' ')} opportunity. "
                    f"{top_opp.description}. Propose a brief 20-min review call."
                ),
                "crm_fields": {
                    "health_score": round(score, 1),
                    "expansion_potential": opportunities.total_expansion_potential,
                    "last_csm_action": "expansion_email",
                },
            }

        elif renewal_soon and risk == ChurnRisk.MEDIUM:
            primary = ActionType.CREATE_TASK
            secondary = [ActionType.SEND_EMAIL]
            confidence = 0.80
            urgency = "high"
            requires_approval = False
            rationale = (
                f"Renewal in {ctx.days_to_renewal} days with medium churn risk. "
                "Proactive renewal task required."
            )
            action_data = {
                "task_title": f"Renewal preparation for {ctx.company_name}",
                "task_description": (
                    f"Contract value ${ctx.contract_value:,.0f}. "
                    f"Renewal on {ctx.renewal.renewal_date}. "
                    "Prepare renewal proposal and schedule call."
                ),
                "email_subject": f"Your upcoming renewal — {ctx.company_name}",
                "email_body_summary": "Renewal reminder with summary of value delivered.",
            }

        else:
            # Healthy, no immediate action — keep CRM updated
            primary = ActionType.UPDATE_CRM
            secondary = []
            confidence = 0.90
            urgency = "low"
            requires_approval = False
            rationale = (
                f"{ctx.company_name} is healthy (score {score:.0f}) with no urgent signals. "
                "Updating CRM record."
            )
            action_data = {
                "crm_fields": {
                    "health_score": round(score, 1),
                    "churn_risk": risk.value,
                    "last_reviewed": datetime.now(timezone.utc).isoformat(),
                }
            }

        return Decision(
            primary_action=primary,
            secondary_actions=secondary,
            confidence=confidence,
            rationale=rationale,
            requires_approval=requires_approval,
            urgency=urgency,
            action_data=action_data,
            decided_at=datetime.now(timezone.utc),
        )
