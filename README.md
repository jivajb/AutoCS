# AutoCS: Multi-Agent Customer Success Engine

A production-style backend system that automates customer success workflows using a multi-agent AI pipeline. Agents analyse customer health, detect churn risk, identify expansion opportunities, and execute actions — with a human-in-the-loop approval gate for high-risk decisions.

---

## Architecture

```
POST /workflows/run/{account_id}
         │
         ▼
   ┌─────────────┐
   │ DataAgent   │  Normalise raw customer JSON → CustomerContext
   └──────┬──────┘
          │
   ┌──────▼──────────┐
   │ AnalysisAgent   │  Health score, churn risk, risk factors, summary
   └──────┬──────────┘
          │
   ┌──────▼─────────────┐
   │ OpportunityAgent   │  Seat expansion, tier upgrade, multi-year, referral
   └──────┬─────────────┘
          │
   ┌──────▼──────────┐
   │ DecisionAgent   │  Primary action + confidence score + rationale
   └──────┬──────────┘
          │
     confidence ≥ threshold          confidence < threshold
     AND risk ≠ critical             OR risk = critical
          │                                  │
   ┌──────▼──────────┐          ┌────────────▼──────────────┐
   │ ActionAgent     │          │ PENDING_REVIEW             │
   │  update_crm     │          │ POST /review/{id}/approve  │
   │  draft_email    │          │ POST /review/{id}/reject   │
   │  slack_alert    │          └────────────────────────────┘
   │  create_task    │
   └─────────────────┘
```

### Component map

| Directory | Purpose |
|---|---|
| `app/agents/` | One file per agent; each has LLM + mock/algorithmic path |
| `app/orchestration/` | `Orchestrator` wires agents, owns the HITL gate, writes traces |
| `app/tools/` | Simulated integrations: CRM, Slack, email, task manager |
| `app/storage/` | SQLite store for runs, traces, and review requests |
| `app/api/` | FastAPI routes |
| `app/models/` | All Pydantic schemas |
| `app/data/` | Mock customer JSON + loader |

### Agent modes

| Mode | When | Behaviour |
|---|---|---|
| **LLM** | `OPENAI_API_KEY` is set | Calls GPT-4o-mini with JSON-mode prompts |
| **Mock** | No API key | Deterministic, rule-based scoring — fully functional without any external calls |

---

## How it works

1. **DataAgent** receives raw customer JSON and derives normalised metrics: `usage_rate`, `feature_adoption_rate`, `days_to_renewal`, open tickets, CSAT, etc.

2. **AnalysisAgent** computes a 0-100 **health score** and categorises **churn risk** as `low / medium / high / critical` using weighted signals (usage trend, ticket severity, satisfaction, renewal proximity).

3. **OpportunityAgent** checks for seat crowding, plan under-utilisation, expansion signals, and multi-year fit to produce a list of typed `Opportunity` objects with estimated ARR.

4. **DecisionAgent** maps risk + opportunity data to a concrete `ActionType` (send_email, create_task, alert_human, update_crm, no_action) with a confidence score and rationale.

5. **HITL gate** — if `confidence < threshold` **or** `churn_risk = critical`, the workflow pauses at `PENDING_REVIEW` and a `ReviewRequest` is stored. A human approves or rejects via API.

6. **ActionAgent** calls the tool layer (CRM, Slack, email, task manager). All tools simulate execution and return structured results that are stored against the run.

Every step appends a `WorkflowStep` to the run's trace, including agent name, input/output summaries, and duration.

---

## Quick start

### Option A — Local (no Docker)

```bash
# 1. Clone & install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — set OPENAI_API_KEY if you want LLM mode, or leave blank for mock mode

# 3. Run
uvicorn app.main:app --reload --port 8000
```

### Option B — Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Visit **http://localhost:8000/docs** for the interactive Swagger UI.

---

## Example API calls

### List all accounts
```bash
curl http://localhost:8000/accounts
```

### Get a specific account
```bash
curl http://localhost:8000/accounts/ACC-003
```

### Run the full pipeline for an account
```bash
curl -X POST http://localhost:8000/workflows/run/ACC-003
```

Response (critical account — paused for review):
```json
{
  "run_id": "a3f1c2d4-...",
  "account_id": "ACC-003",
  "status": "pending_review",
  "message": "Workflow paused — awaiting human review."
}
```

### List pending reviews
```bash
curl http://localhost:8000/reviews/pending
```

### Approve an action
```bash
curl -X POST http://localhost:8000/review/a3f1c2d4-.../approve \
  -H "Content-Type: application/json" \
  -d '{"reviewer_note": "Confirmed — escalate to VP level"}'
```

### Reject an action
```bash
curl -X POST http://localhost:8000/review/a3f1c2d4-.../reject \
  -H "Content-Type: application/json" \
  -d '{"reviewer_note": "Not ready — check with account manager first"}'
```

### Inspect the execution trace
```bash
curl http://localhost:8000/runs/a3f1c2d4-.../trace
```

### Run a healthy/expansion account (auto-executes)
```bash
curl -X POST http://localhost:8000/workflows/run/ACC-005
# → status: completed, action_results include CRM update + expansion email
```

---

## Mock accounts (highlights)

| Account | Scenario | Expected outcome |
|---|---|---|
| ACC-001 TechVision Inc | High usage, expansion signals, renewal Q3 | Expansion email auto-sent |
| ACC-002 RetailEdge Co | Declining, critical ticket, renewal in 35 days | High churn, email + task |
| ACC-003 HealthBridge | Critical: 2 critical tickets, 15% usage rate | PENDING_REVIEW (critical) |
| ACC-005 MegaBank | Healthy flagship, renewal in 75 days, strong signals | Expansion email + multi-year pitch |
| ACC-007 LogisticsPro | 95% seat capacity, strong expansion signal | Seat expansion email |
| ACC-010 InsureTech | Perfect health, referral champion | Multi-year + referral actions |

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | _(empty)_ | Leave blank for mock mode |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model to use |
| `HITL_CONFIDENCE_THRESHOLD` | `0.7` | Below this confidence → human approval required |
| `DB_PATH` | `autocs.db` | SQLite database file path |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DEBUG` | `false` | Enable uvicorn auto-reload |

---

## Project structure

```
AutoCS/
├── app/
│   ├── main.py                    # FastAPI app + lifespan
│   ├── config.py                  # Pydantic settings
│   ├── agents/
│   │   ├── base.py                # BaseAgent + LLM helper
│   │   ├── data_agent.py          # Normalisation (no LLM)
│   │   ├── analysis_agent.py      # Health score + churn risk
│   │   ├── opportunity_agent.py   # Expansion signals
│   │   ├── decision_agent.py      # Action decision
│   │   └── action_agent.py        # Tool dispatch
│   ├── orchestration/
│   │   └── orchestrator.py        # Pipeline + HITL gate
│   ├── tools/
│   │   └── actions.py             # CRM / Slack / email / tasks (simulated)
│   ├── storage/
│   │   └── store.py               # SQLite persistence
│   ├── api/
│   │   └── routes.py              # FastAPI endpoints
│   ├── models/
│   │   ├── customer.py            # RawCustomerData, CustomerContext
│   │   ├── agents.py              # HealthAnalysis, Decision, ActionResult …
│   │   └── workflow.py            # WorkflowRun, ReviewRequest
│   └── data/
│       ├── mock_customers.json    # 12 realistic accounts
│       └── loader.py              # Seeds the store at startup
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```
