"""
Orchestrator — wires all agents together into a single, traceable pipeline.

Pipeline:
  RawCustomerData
      ↓ DataAgent
  CustomerContext
      ↓ AnalysisAgent
  HealthAnalysis
      ↓ OpportunityAgent
  OpportunityAnalysis
      ↓ DecisionAgent
  Decision
      ↓ ActionAgent  (skipped if requires_approval)
  List[ActionResult]
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.agents.action_agent import ActionAgent
from app.agents.analysis_agent import AnalysisAgent
from app.agents.data_agent import DataAgent
from app.agents.decision_agent import DecisionAgent
from app.agents.opportunity_agent import OpportunityAgent
from app.config import Settings
from app.models.customer import RawCustomerData
from app.models.workflow import ReviewRequest, RunStatus, WorkflowRun, WorkflowStep
from app.storage.store import Store

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config: Settings, store: Store):
        self.config = config
        self.store = store

        self.data_agent = DataAgent(config)
        self.analysis_agent = AnalysisAgent(config)
        self.opportunity_agent = OpportunityAgent(config)
        self.decision_agent = DecisionAgent(config)
        self.action_agent = ActionAgent(config)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, raw: RawCustomerData) -> WorkflowRun:
        run_id = str(uuid.uuid4())
        run = WorkflowRun(
            run_id=run_id,
            account_id=raw.account_id,
            status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self.store.save_run(run)
        logger.info("=== Workflow %s started for account %s ===", run_id, raw.account_id)

        try:
            run = self._execute(run, raw)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Workflow %s failed: %s", run_id, exc)
            run.status = RunStatus.FAILED
            run.error = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            self.store.save_run(run)

        logger.info(
            "=== Workflow %s finished | status=%s ===", run_id, run.status.value
        )
        return run

    # ── Pipeline execution ────────────────────────────────────────────────────

    def _execute(self, run: WorkflowRun, raw: RawCustomerData) -> WorkflowRun:
        # ── Step 1: DataAgent ─────────────────────────────────────────────────
        ctx = self._timed_step(
            run,
            step_name="data_normalisation",
            agent_name="DataAgent",
            fn=lambda: self.data_agent.run(raw),
            input_summary=f"Raw data for {raw.company_name}",
            output_summary_fn=lambda r: (
                f"usage_rate={r.usage_rate:.2f} adoption={r.feature_adoption_rate:.2f} "
                f"days_to_renewal={r.days_to_renewal}"
            ),
        )

        # ── Step 2: AnalysisAgent ──────────────────────────────────────────────
        health = self._timed_step(
            run,
            step_name="health_analysis",
            agent_name="AnalysisAgent",
            fn=lambda: self.analysis_agent.run(ctx),
            input_summary=f"CustomerContext for {ctx.company_name}",
            output_summary_fn=lambda r: (
                f"health_score={r.health_score} churn_risk={r.churn_risk.value}"
            ),
        )
        run.health_analysis = health.model_dump(mode="json")
        self.store.save_run(run)

        # ── Step 3: OpportunityAgent ───────────────────────────────────────────
        opps = self._timed_step(
            run,
            step_name="opportunity_analysis",
            agent_name="OpportunityAgent",
            fn=lambda: self.opportunity_agent.run(ctx, health),
            input_summary="CustomerContext + HealthAnalysis",
            output_summary_fn=lambda r: (
                f"{len(r.opportunities)} opportunities | potential=${r.total_expansion_potential:,.0f}"
            ),
        )
        run.opportunities = opps.model_dump(mode="json")
        self.store.save_run(run)

        # ── Step 4: DecisionAgent ──────────────────────────────────────────────
        decision = self._timed_step(
            run,
            step_name="decision",
            agent_name="DecisionAgent",
            fn=lambda: self.decision_agent.run(ctx, health, opps),
            input_summary="CustomerContext + HealthAnalysis + OpportunityAnalysis",
            output_summary_fn=lambda r: (
                f"action={r.primary_action.value} confidence={r.confidence:.2f} "
                f"requires_approval={r.requires_approval}"
            ),
        )
        run.decision = decision.model_dump(mode="json")
        self.store.save_run(run)

        # ── HITL gate ──────────────────────────────────────────────────────────
        if decision.requires_approval:
            run.status = RunStatus.PENDING_REVIEW
            run.requires_review = True
            run.review_reason = (
                f"confidence={decision.confidence:.2f} urgency={decision.urgency} "
                f"churn_risk={health.churn_risk.value}"
            )

            review = ReviewRequest(
                run_id=run.run_id,
                account_id=run.account_id,
                company_name=raw.company_name,
                decision=decision.model_dump(mode="json"),
                health_analysis=health.model_dump(mode="json"),
                created_at=datetime.now(timezone.utc),
            )
            self.store.save_review(review)
            self.store.save_run(run)
            logger.info(
                "Workflow %s paused for human review (confidence=%.2f urgency=%s)",
                run.run_id,
                decision.confidence,
                decision.urgency,
            )
            return run

        # ── Step 5: ActionAgent ────────────────────────────────────────────────
        results = self._timed_step(
            run,
            step_name="action_execution",
            agent_name="ActionAgent",
            fn=lambda: self.action_agent.run(ctx, decision),
            input_summary=f"Decision: {decision.primary_action.value}",
            output_summary_fn=lambda r: (
                f"{len(r)} actions executed — "
                + ", ".join(f"{a.action_type.value}:{a.status}" for a in r)
            ),
        )
        run.action_results = [r.model_dump(mode="json") for r in results]
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        self.store.save_run(run)
        return run

    # ── Post-approval execution ───────────────────────────────────────────────

    def execute_approved(self, run: WorkflowRun) -> WorkflowRun:
        """
        Called after a human approves a pending-review workflow.
        Reconstructs context from stored data and runs ActionAgent.
        """
        from app.models.agents import Decision  # noqa: PLC0415

        raw_data = self.store.get_account(run.account_id)
        if raw_data is None:
            raise ValueError(f"Account {run.account_id} not found")

        raw = RawCustomerData.model_validate(raw_data)
        ctx = self.data_agent.run(raw)
        decision = Decision.model_validate(run.decision)

        results = self.action_agent.run(ctx, decision)
        run.action_results = [r.model_dump(mode="json") for r in results]
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        self.store.save_run(run)
        return run

    # ── Timing / tracing helper ───────────────────────────────────────────────

    def _timed_step(self, run: WorkflowRun, step_name: str, agent_name: str, fn, input_summary: str, output_summary_fn):
        t0 = time.monotonic()
        result = fn()
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        step = WorkflowStep(
            step_name=step_name,
            agent=agent_name,
            input_summary=input_summary,
            output_summary=output_summary_fn(result),
            duration_ms=elapsed_ms,
            timestamp=datetime.now(timezone.utc),
        )
        run.steps.append(step)
        logger.info(
            "[%s] %s completed in %dms → %s",
            agent_name,
            step_name,
            elapsed_ms,
            step.output_summary,
        )
        return result
