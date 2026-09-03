"""FastAPI app factory — Crumbs attribution ledger."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .config import get_settings
from .db.session import create_all
from .signing import SigningService
from .stores import build_stores

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("crumbs")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.signing = SigningService()
    nonce_store, rate_limiter = build_stores()
    app.state.nonce_store = nonce_store
    app.state.rate_limiter = rate_limiter
    app.state.redis_connected = bool(settings.redis_url)
    # Dev convenience: create tables from models when the DB is SQLite.
    # Production uses server/migrations/0001_init.sql (Postgres).
    if settings.database_url.startswith("sqlite"):
        create_all()
        log.info("SQLite dev database ready (tables created from models)")
    log.info("Crumbs ledger v0.1 starting (stores=%s)",
             "redis" if app.state.redis_connected else "memory")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Neutral agent-journey attribution + referral-settlement ledger "
            "(signed-attribution receipts v1). Local MVP build — nothing published."
        ),
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
