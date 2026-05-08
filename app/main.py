"""
AutoCS – Multi-Agent Customer Success Engine
FastAPI application entry point.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.data.loader import load_mock_data
from app.orchestration.orchestrator import Orchestrator
from app.storage.store import Store

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AutoCS…")

    store = Store(settings.db_path)
    store.initialize()
    load_mock_data(store)

    orchestrator = Orchestrator(config=settings, store=store)

    app.state.store = store
    app.state.orchestrator = orchestrator

    logger.info(
        "AutoCS ready | mode=%s | db=%s",
        "llm" if settings.use_llm else "mock",
        settings.db_path,
    )
    yield
    logger.info("AutoCS shutting down")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AutoCS: Multi-Agent Customer Success Engine",
    description=(
        "A production-style multi-agent AI system that automates customer success workflows: "
        "churn detection, health scoring, expansion identification, and automated action execution "
        "with human-in-the-loop approval gates."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", tags=["health"])
def root():
    return {
        "service": "AutoCS",
        "version": "1.0.0",
        "mode": "llm" if settings.use_llm else "mock",
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
