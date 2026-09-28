"""Orologio del servizio: sostituibile nei test tramite create_app(clock=...)."""
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
