"""Utilizzo dei provider per mese: secondi di audio trascritti e chiamate (spec §7, §8)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import AiUsage


def month_start(now: datetime, tz: ZoneInfo) -> date:
    """Primo giorno del mese nel fuso dell'archivio: il mese è quello che vede l'utente."""
    return now.astimezone(tz).date().replace(day=1)


def record_usage(s: Session, provider: str, month: date, audio_seconds: float,
                 calls: int = 1) -> None:
    stmt = pg_insert(AiUsage).values(provider=provider, month=month,
                                     audio_seconds=audio_seconds, calls=calls)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AiUsage.provider, AiUsage.month],
        set_={"audio_seconds": AiUsage.audio_seconds + stmt.excluded.audio_seconds,
              "calls": AiUsage.calls + stmt.excluded.calls})
    s.execute(stmt)


def usage_for_month(s: Session, month: date) -> dict[str, AiUsage]:
    return {row.provider: row for row in s.scalars(select(AiUsage).where(AiUsage.month == month))}
