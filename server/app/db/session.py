"""Database session + table creation helpers."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..config import get_settings
from .models import Base

_engine = None
_SessionLocal: sessionmaker | None = None


def _engine_kwargs(url: str) -> dict:
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url == "sqlite://":
            # In-memory SQLite: every pooled connection must share the SAME DB
            kwargs["poolclass"] = StaticPool
    return kwargs


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_engine(url, **_engine_kwargs(url))
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def create_all() -> None:
    """Create tables from models (dev/tests). Production uses migrations/."""
    Base.metadata.create_all(get_engine())


def reset_engine_for_tests(url: str = "sqlite://") -> None:
    """Test hook: point the engine at a fresh in-memory SQLite."""
    global _engine, _SessionLocal
    _engine = create_engine(url, **_engine_kwargs(url))
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def get_db():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def audit(db: Session, event_type: str, entity_type: str, entity_id: str,
          actor: str = "system", payload: dict | None = None) -> None:
    """Append an audit event (append-only by design)."""
    import json

    from .models import AuditEvent

    db.add(
        AuditEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            payload=json.dumps(payload or {}, sort_keys=True),
        )
    )
