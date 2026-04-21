"""
Tool layer — simulates real integrations (CRM, Slack, email, task manager).

In production each function would call the real external API.
Here we log the call and return a structured result so the rest of
the system behaves exactly as it would with live integrations.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)


# ── CRM ───────────────────────────────────────────────────────────────────────

def update_crm_record(account_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update fields on the CRM record for *account_id*.

    Simulates a PATCH call to e.g. Salesforce or HubSpot.
    """
    record_id = f"CRM-{account_id}"
    logger.info("[tool:update_crm_record] Updating %s with fields: %s", record_id, fields)
    return {
        "crm_record_id": record_id,
        "updated_fields": list(fields.keys()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "simulated": True,
    }


# ── Slack ─────────────────────────────────────────────────────────────────────

def create_slack_alert(
    account_id: str,
    company_name: str,
    message: str,
    urgency: str = "high",
    channel: str = "#cs-alerts",
) -> Dict[str, Any]:
    """
    Post an alert to the Customer Success Slack channel.

    Simulates a POST to Slack's chat.postMessage API.
    """
    emoji_map = {"low": "ℹ️", "medium": "⚠️", "high": "🔴", "immediate": "🚨"}
    emoji = emoji_map.get(urgency, "⚠️")
    formatted = f"{emoji} *[{urgency.upper()}]* Account: *{company_name}* ({account_id})\n{message}"
    msg_ts = f"{datetime.now(timezone.utc).timestamp():.6f}"

    logger.info("[tool:create_slack_alert] → %s\n%s", channel, formatted)
    return {
        "channel": channel,
        "message_ts": msg_ts,
        "text": formatted,
        "simulated": True,
    }


# ── Email ─────────────────────────────────────────────────────────────────────

def draft_email(
    account_id: str,
    to_name: str,
    to_company: str,
    subject: str,
    body_summary: str,
    from_name: str = "Customer Success Team",
    from_email: str = "cs@autocs.io",
) -> Dict[str, Any]:
    """
    Draft and queue an outbound email.

    Simulates sending via SendGrid / Mailgun / SES.
    The body_summary is stored verbatim here; a real integration
    would expand it through a template engine.
    """
    message_id = f"email-{uuid.uuid4().hex[:8]}"
    logger.info(
        "[tool:draft_email] Drafting email to %s <%s> | subject: %s",
        to_name,
        to_company,
        subject,
    )
    return {
        "message_id": message_id,
        "from": f"{from_name} <{from_email}>",
        "to_company": to_company,
        "subject": subject,
        "body_summary": body_summary,
        "status": "queued",
        "simulated": True,
    }


# ── Task Manager ──────────────────────────────────────────────────────────────

def create_followup_task(
    account_id: str,
    title: str,
    description: str,
    urgency: str = "medium",
    assignee: str = "account_manager",
) -> Dict[str, Any]:
    """
    Create a follow-up task in the task management system.

    Simulates a call to Asana / Linear / Jira.
    """
    priority_map = {"low": "P3", "medium": "P2", "high": "P1", "immediate": "P0"}
    task_id = f"TASK-{uuid.uuid4().hex[:6].upper()}"
    due_offset = {"low": 14, "medium": 7, "high": 3, "immediate": 1}
    from datetime import timedelta

    due_date = (datetime.now(timezone.utc) + timedelta(days=due_offset.get(urgency, 7))).date()

    logger.info("[tool:create_followup_task] Created %s | %s | %s", task_id, priority_map.get(urgency, "P2"), title)
    return {
        "task_id": task_id,
        "title": title,
        "description": description,
        "priority": priority_map.get(urgency, "P2"),
        "assignee": assignee,
        "due_date": str(due_date),
        "status": "open",
        "simulated": True,
    }
