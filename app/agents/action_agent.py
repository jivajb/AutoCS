from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.agents.base import BaseAgent
from app.config import Settings
from app.models.agents import ActionResult, ActionType, Decision
from app.models.customer import CustomerContext
from app.tools.actions import (
    create_followup_task,
    create_slack_alert,
    draft_email,
    update_crm_record,
)

logger = logging.getLogger(__name__)


class ActionAgent(BaseAgent):
    """
    Executes the decided actions by calling the tool layer.
    Returns a list of ActionResult objects — one per action executed.
    """

    name = "ActionAgent"

    def __init__(self, config: Settings):
        super().__init__(config)

    def run(self, ctx: CustomerContext, decision: Decision) -> List[ActionResult]:
        t0 = self._timer()
        logger.info(
            "[ActionAgent] Executing actions for account %s | primary=%s secondary=%s",
            ctx.account_id,
            decision.primary_action,
            [a.value for a in decision.secondary_actions],
        )

        all_actions = [decision.primary_action] + decision.secondary_actions
        results: List[ActionResult] = []

        for action in all_actions:
            result = self._execute(action, ctx, decision)
            results.append(result)
            logger.info(
                "[ActionAgent] %s → status=%s", action.value, result.status
            )

        elapsed = self._elapsed_ms(t0)
        logger.info(
            "[ActionAgent] Completed %d actions in %dms for account %s",
            len(results),
            elapsed,
            ctx.account_id,
        )
        return results

    # ── Dispatcher ────────────────────────────────────────────────────────────

    def _execute(
        self, action: ActionType, ctx: CustomerContext, decision: Decision
    ) -> ActionResult:
        ad = decision.action_data
        now = datetime.now(timezone.utc)

        try:
            if action == ActionType.SEND_EMAIL:
                res = draft_email(
                    account_id=ctx.account_id,
                    to_name=ctx.account_manager,
                    to_company=ctx.company_name,
                    subject=ad.get("email_subject", f"Follow-up: {ctx.company_name}"),
                    body_summary=ad.get("email_body_summary", decision.rationale),
                )

            elif action == ActionType.CREATE_TASK:
                res = create_followup_task(
                    account_id=ctx.account_id,
                    title=ad.get("task_title", f"Follow-up: {ctx.company_name}"),
                    description=ad.get("task_description", decision.rationale),
                    urgency=decision.urgency,
                )

            elif action == ActionType.ALERT_HUMAN:
                res = create_slack_alert(
                    account_id=ctx.account_id,
                    company_name=ctx.company_name,
                    message=ad.get("alert_message", decision.rationale),
                    urgency=decision.urgency,
                )

            elif action == ActionType.UPDATE_CRM:
                res = update_crm_record(
                    account_id=ctx.account_id,
                    fields=ad.get("crm_fields", {}),
                )

            elif action == ActionType.NO_ACTION:
                res = {"message": "No action taken — account is in good standing"}

            else:
                res = {"message": f"Unknown action type: {action}"}

            return ActionResult(
                action_type=action,
                status="simulated",
                result=res,
                executed_at=now,
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("[ActionAgent] Failed to execute %s", action)
            return ActionResult(
                action_type=action,
                status="failed",
                result={},
                executed_at=now,
                error=str(exc),
            )
