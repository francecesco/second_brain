"""Contesto per estrarre le azioni (spec azioni §4): quando è stata registrata la nota e gli
impegni dei giorni dopo, dall'istantanea di Home Assistant già letta dal worker. Puro."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .ha_store import Snapshot
from .web.templating import MONTHS, WEEKDAYS

CONTEXT_DAYS = 14
MAX_EVENTS = 30
MAX_EVENT_CHARS = 80


def _when(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def _event_start(value: str, tz: ZoneInfo) -> tuple[date, str] | None:
    """(giorno, ora o "") di un evento come arriva da Home Assistant; None se illeggibile."""
    try:
        if len(value) <= 10:
            return date.fromisoformat(value), ""
        start = datetime.fromisoformat(value)
    except ValueError:
        return None
    if start.tzinfo is not None:
        start = start.astimezone(tz)
    return start.date(), f" alle {start:%H:%M}"


def action_context(recorded_at: datetime, snapshot: Snapshot | None, tz: ZoneInfo) -> tuple[str, ...]:
    local = recorded_at.astimezone(tz)
    lines = [f"La nota è stata registrata {_when(local.date())} alle {local:%H:%M}."]
    reading = snapshot.reading if snapshot is not None else None
    if reading is None:
        return tuple(lines)
    first, last = local.date(), local.date() + timedelta(days=CONTEXT_DAYS)
    items: list[tuple[date, str, str]] = []
    for event in reading.events:
        start = _event_start(event.start, tz)
        if start is None or not first <= start[0] <= last:
            continue
        summary = " ".join(str(event.summary).split())[:MAX_EVENT_CHARS]
        if summary:
            items.append((start[0], start[1], summary))
    items.sort()
    if items:
        shown = "; ".join(f"{_when(d)}{hour}: {summary}" for d, hour, summary in items[:MAX_EVENTS])
        lines.append(f"Impegni in calendario nei giorni seguenti: {shown}.")
    return tuple(lines)
