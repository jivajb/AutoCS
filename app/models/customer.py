from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class PlanTier(str, Enum):
    STARTER = "starter"
    GROWTH = "growth"
    ENTERPRISE = "enterprise"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"
    CLOSED = "closed"


class SupportTicket(BaseModel):
    id: str
    subject: str
    status: TicketStatus
    priority: TicketPriority
    created_at: datetime
    resolved_at: Optional[datetime] = None
    satisfaction_score: Optional[int] = Field(None, ge=1, le=5)


class UsageMetrics(BaseModel):
    monthly_active_users: int
    total_seats: int
    features_adopted: int
    total_features: int
    api_calls_last_30d: int
    last_login_days_ago: int
    usage_trend: str  # increasing | stable | declining


class RenewalInfo(BaseModel):
    renewal_date: date
    contract_value: float
    auto_renew: bool


class ExpansionSignal(BaseModel):
    signal_type: str  # new_department | high_usage | feature_request | referral | hiring
    description: str
    strength: str  # weak | moderate | strong


class RawCustomerData(BaseModel):
    """Raw customer record as stored in the mock data file."""
    account_id: str
    company_name: str
    industry: str
    company_size: str  # SMB | Mid-Market | Enterprise
    plan_tier: PlanTier
    account_manager: str
    support_tickets: List[SupportTicket]
    usage: UsageMetrics
    renewal: RenewalInfo
    expansion_signals: List[ExpansionSignal]
    notes: Optional[str] = None


class CustomerContext(BaseModel):
    """Normalised customer context produced by DataAgent."""
    account_id: str
    company_name: str
    industry: str
    company_size: str
    plan_tier: PlanTier
    account_manager: str

    # Derived metrics
    usage_rate: float = Field(..., description="MAU / total_seats")
    feature_adoption_rate: float = Field(..., description="features_adopted / total_features")
    open_tickets: int
    critical_tickets: int
    avg_satisfaction_score: Optional[float]
    days_to_renewal: int
    contract_value: float

    # Signals
    expansion_signals: List[ExpansionSignal]

    # Raw sub-objects for downstream agents
    raw_usage: UsageMetrics
    renewal: RenewalInfo

    normalized_at: datetime
