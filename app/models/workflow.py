from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class RunStatus(str, Enum):
    RUNNING = "running"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class WorkflowStep(BaseModel):
    step_name: str
    agent: str
    input_summary: str
    output_summary: str
    duration_ms: int
    timestamp: datetime
    metadata: Dict[str, Any] = {}


class WorkflowRun(BaseModel):
    run_id: str
    account_id: str
    status: RunStatus
    started_at: datetime
    completed_at: Optional[datetime] = None

    # Agent outputs (serialised as dicts for storage flexibility)
    health_analysis: Optional[Dict[str, Any]] = None
    opportunities: Optional[Dict[str, Any]] = None
    decision: Optional[Dict[str, Any]] = None
    action_results: List[Dict[str, Any]] = []

    # Execution trace
    steps: List[WorkflowStep] = []

    # Review
    requires_review: bool = False
    review_reason: Optional[str] = None

    error: Optional[str] = None


class ReviewRequest(BaseModel):
    run_id: str
    account_id: str
    company_name: str
    decision: Dict[str, Any]
    health_analysis: Dict[str, Any]
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    approved: Optional[bool] = None
    reviewer_note: Optional[str] = None
