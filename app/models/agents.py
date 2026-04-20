from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── AnalysisAgent ──────────────────────────────────────────────────────────────

class ChurnRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HealthAnalysis(BaseModel):
    health_score: float = Field(..., ge=0, le=100, description="0-100 composite health score")
    churn_risk: ChurnRisk
    risk_factors: List[str]
    positive_signals: List[str]
    summary: str
    analyzed_at: datetime


# ── OpportunityAgent ───────────────────────────────────────────────────────────

class OpportunityType(str, Enum):
    SEAT_EXPANSION = "seat_expansion"
    TIER_UPGRADE = "tier_upgrade"
    CROSS_SELL = "cross_sell"
    MULTI_YEAR = "multi_year"
    REFERRAL = "referral"


class Opportunity(BaseModel):
    type: OpportunityType
    description: str
    estimated_value: float = Field(..., description="Estimated incremental ARR in USD")
    confidence: float = Field(..., ge=0, le=1)
    rationale: str


class OpportunityAnalysis(BaseModel):
    opportunities: List[Opportunity]
    total_expansion_potential: float
    priority: str  # low | medium | high
    analyzed_at: datetime


# ── DecisionAgent ──────────────────────────────────────────────────────────────

class ActionType(str, Enum):
    SEND_EMAIL = "send_email"
    CREATE_TASK = "create_task"
    ALERT_HUMAN = "alert_human"
    UPDATE_CRM = "update_crm"
    NO_ACTION = "no_action"


class Decision(BaseModel):
    primary_action: ActionType
    secondary_actions: List[ActionType] = []
    confidence: float = Field(..., ge=0, le=1)
    rationale: str
    requires_approval: bool
    urgency: str  # low | medium | high | immediate
    action_data: Dict[str, Any] = Field(default_factory=dict)
    decided_at: datetime


# ── ActionAgent ────────────────────────────────────────────────────────────────

class ActionResult(BaseModel):
    action_type: ActionType
    status: str  # success | failed | simulated
    result: Dict[str, Any]
    executed_at: datetime
    error: Optional[str] = None
