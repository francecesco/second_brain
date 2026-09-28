"""Nomi e cartelle dell'archivio (spec §4, §6): funzioni pure, nessun I/O."""
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_DEVICE_TYPE = "epaper154"
CAPTURE_ID_RE = re.compile(r"cap_(\d{8}_\d{6}|unsynced_\d{6})(_\d+)?")
DEVICE_ID_RE = re.compile(r"[0-9a-f]{12}")
DEVICE_TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
DAY_DIR_RE = re.compile(r"\d{4}/\d{2}/\d{2}")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MIN_PLAUSIBLE_YEAR = 2024
MAX_FUTURE = timedelta(hours=24)


def is_valid_capture_id(value: str) -> bool:
    return CAPTURE_ID_RE.fullmatch(value) is not None


def is_valid_device_id(value: str) -> bool:
    return DEVICE_ID_RE.fullmatch(value) is not None


def is_valid_device_type(value: str) -> bool:
    return DEVICE_TYPE_RE.fullmatch(value) is not None


def parse_capture_ts(header: str | None, now_utc: datetime) -> datetime | None:
    """X-Capture-Ts se ben formato e plausibile, altrimenti None."""
    if not header:
        return None
    try:
        ts = datetime.strptime(header, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    if ts.year < MIN_PLAUSIBLE_YEAR or ts > now_utc + MAX_FUTURE:
        return None
    return ts


def resolve_recorded_at(header: str | None, now_utc: datetime) -> tuple[datetime, bool]:
    """(istante di registrazione in UTC, data_stimata)."""
    ts = parse_capture_ts(header, now_utc)
    if ts is None:
        return now_utc, True
    return ts, False


def local_day(utc: datetime, tz: ZoneInfo) -> date:
    return utc.astimezone(tz).date()


def day_dir(day: date) -> str:
    return f"{day:%Y/%m/%d}"


def parse_day_dir(value: str) -> date:
    if DAY_DIR_RE.fullmatch(value) is None:
        raise ValueError(f"cartella del giorno non valida: {value!r}")
    return datetime.strptime(value, "%Y/%m/%d").date()


def base_name(utc: datetime, tz: ZoneInfo, device_id: str) -> str:
    return f"{utc.astimezone(tz):%H%M%S}_{device_id}"


def with_suffix(base: str, k: int) -> str:
    return base if k <= 1 else f"{base}_{k}"


def unique_base(base: str, taken: Callable[[str], bool]) -> str:
    k = 1
    while taken(with_suffix(base, k)):
        k += 1
    return with_suffix(base, k)
