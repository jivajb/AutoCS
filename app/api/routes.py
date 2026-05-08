from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app.models.customer import RawCustomerData
from app.models.workflow import ReviewRequest, RunStatus, WorkflowRun
from app.orchestration.orchestrator import Orchestrator
from app.storage.store import Store

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Dependency helpers ────────────────────────────────────────────────────────

def get_store(request: Request) -> Store:
    return request.app.state.store


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


# ── Response models ───────────────────────────────────────────────────────────

class AccountSummary(BaseModel):
    account_id: str
    company_name: str
    industry: str
    company_size: str
    plan_tier: str
    contract_value: float
    renewal_date: str
    account_manager: str


class RunStartResponse(BaseModel):
    run_id: str
    account_id: str
    status: str
    message: str


class ReviewActionRequest(BaseModel):
    reviewer_note: Optional[str] = None


# ── Accounts ──────────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=List[AccountSummary], tags=["accounts"])
def list_accounts(store: Store = Depends(get_store)):
    """Return a summary of all customer accounts."""
    accounts = store.list_accounts()
    return [
        AccountSummary(
            account_id=a["account_id"],
            company_name=a["company_name"],
            industry=a["industry"],
            company_size=a["company_size"],
            plan_tier=a["plan_tier"],
            contract_value=a["renewal"]["contract_value"],
            renewal_date=a["renewal"]["renewal_date"],
            account_manager=a["account_manager"],
        )
        for a in accounts
    ]


@router.get("/accounts/{account_id}", tags=["accounts"])
def get_account(account_id: str, store: Store = Depends(get_store)):
    """Return full raw data for a single account."""
    account = store.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    return account


# ── Workflows ─────────────────────────────────────────────────────────────────

def _run_workflow(raw: RawCustomerData, orchestrator: Orchestrator) -> None:
    """Background task wrapper."""
    orchestrator.run(raw)


@router.post(
    "/workflows/run/{account_id}",
    response_model=RunStartResponse,
    status_code=202,
    tags=["workflows"],
)
def run_workflow(
    account_id: str,
    background_tasks: BackgroundTasks,
    store: Store = Depends(get_store),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """
    Trigger a full multi-agent workflow for the given account.

    The pipeline runs synchronously (simple demo behaviour) but is
    wired through BackgroundTasks so the response returns a run_id
    immediately — swap to Celery/ARQ for true async in production.
    """
    raw_data = store.get_account(account_id)
    if not raw_data:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")

    raw = RawCustomerData.model_validate(raw_data)

    # Run synchronously for simplicity; the run_id is assigned inside orchestrator
    run = orchestrator.run(raw)

    return RunStartResponse(
        run_id=run.run_id,
        account_id=run.account_id,
        status=run.status.value,
        message=(
            "Workflow completed."
            if run.status == RunStatus.COMPLETED
            else "Workflow paused — awaiting human review."
            if run.status == RunStatus.PENDING_REVIEW
            else f"Workflow status: {run.status.value}"
        ),
    )


# ── Runs ──────────────────────────────────────────────────────────────────────

@router.get("/runs", response_model=List[Dict[str, Any]], tags=["runs"])
def list_runs(
    account_id: Optional[str] = None,
    store: Store = Depends(get_store),
):
    """List all workflow runs, optionally filtered by account."""
    runs = store.list_runs(account_id=account_id)
    return [
        {
            "run_id": r.run_id,
            "account_id": r.account_id,
            "status": r.status.value,
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "requires_review": r.requires_review,
            "steps": len(r.steps),
        }
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=Dict[str, Any], tags=["runs"])
def get_run(run_id: str, store: Store = Depends(get_store)):
    """Return the full workflow run including agent outputs."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run.model_dump(mode="json")


@router.get("/runs/{run_id}/trace", response_model=List[Dict[str, Any]], tags=["runs"])
def get_run_trace(run_id: str, store: Store = Depends(get_store)):
    """Return the step-by-step execution trace for a workflow run."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return [s.model_dump(mode="json") for s in run.steps]


# ── Human-in-the-loop review ──────────────────────────────────────────────────

@router.get("/reviews/pending", response_model=List[Dict[str, Any]], tags=["review"])
def list_pending_reviews(store: Store = Depends(get_store)):
    """List all workflow runs that are awaiting human approval."""
    runs = store.list_pending_reviews()
    reviews = []
    for run in runs:
        review = store.get_review(run.run_id)
        reviews.append(
            {
                "run_id": run.run_id,
                "account_id": run.account_id,
                "review_reason": run.review_reason,
                "decision": run.decision,
                "health_analysis": run.health_analysis,
                "created_at": run.started_at.isoformat(),
                "review_note": review.reviewer_note if review else None,
            }
        )
    return reviews


@router.post("/review/{run_id}/approve", tags=["review"])
def approve_action(
    run_id: str,
    body: ReviewActionRequest,
    store: Store = Depends(get_store),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """
    Approve a pending workflow and execute its actions.

    If a reviewer note is provided it is recorded against the review.
    """
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status != RunStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not pending review (status={run.status.value})",
        )

    # Update review record
    review = store.get_review(run_id)
    if review:
        review.approved = True
        review.reviewed_at = datetime.now(timezone.utc)
        review.reviewer_note = body.reviewer_note
        store.update_review(review)

    # Execute actions
    run = orchestrator.execute_approved(run)

    return {
        "run_id": run_id,
        "status": run.status.value,
        "action_results": run.action_results,
        "message": "Actions approved and executed.",
    }


@router.post("/review/{run_id}/reject", tags=["review"])
def reject_action(
    run_id: str,
    body: ReviewActionRequest,
    store: Store = Depends(get_store),
):
    """
    Reject a pending workflow — no actions will be executed.
    """
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status != RunStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not pending review (status={run.status.value})",
        )

    review = store.get_review(run_id)
    if review:
        review.approved = False
        review.reviewed_at = datetime.now(timezone.utc)
        review.reviewer_note = body.reviewer_note
        store.update_review(review)

    run.status = RunStatus.REJECTED
    run.completed_at = datetime.now(timezone.utc)
    store.save_run(run)

    return {
        "run_id": run_id,
        "status": RunStatus.REJECTED.value,
        "message": "Actions rejected. No execution performed.",
    }
