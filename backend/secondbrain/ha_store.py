"""Configurazione di Home Assistant, token cifrato e ultima lettura in Postgres.

Come le chiavi dei provider, il token si cifra con SETTINGS_KEY. L'istantanea è l'ultima
lettura riuscita più l'ultimo errore: la UI e il digest leggono da qui, solo il worker
parla con Home Assistant.
"""
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from . import homeassistant as ha
from .settings_store import (NO_KEY, UNREADABLE_KEY, SecretBox, get_setting,
                             mask_key, set_setting)

CONFIG_KEY = "homeassistant"
TOKEN_KEY = "homeassistant_token"
SNAPSHOT_KEY = "homeassistant_snapshot"
REFRESH_KEY = "homeassistant_refresh"
MAX_LABEL_LEN = 24


@dataclass(frozen=True)
class Snapshot:
    fetched_at: datetime | None          # ultima lettura riuscita
    reading: ha.Reading | None
    error: str | None = None             # ultimo errore (None dopo una lettura riuscita)
    error_at: datetime | None = None


def _clean_label(label: str | None) -> str | None:
    text = " ".join((label or "").split())[:MAX_LABEL_LEN].rstrip()
    return text or None


def _config_data(s: Session) -> dict | None:
    data = get_setting(s, CONFIG_KEY, None)
    return data if isinstance(data, dict) and data.get("url") else None


def get_config(s: Session) -> ha.HaConfig | None:
    data = _config_data(s)
    if data is None:
        return None
    try:
        return ha.HaConfig(
            url=str(data["url"]),
            calendars=tuple(str(c) for c in data.get("calendars") or ()),
            sensors=tuple(ha.SensorChoice(str(x["entity_id"]), x.get("label"))
                          for x in data.get("sensors") or ()),
            shopping_list=data.get("shopping_list") or None)
    except (KeyError, TypeError, AttributeError):
        return None


def _put_config(s: Session, config: ha.HaConfig) -> None:
    set_setting(s, CONFIG_KEY, {
        "url": config.url, "calendars": list(config.calendars),
        "sensors": [asdict(x) for x in config.sensors], "shopping_list": config.shopping_list})


def save_connection(s: Session, box: SecretBox, url: str, token: str | None) -> None:
    """`token` vuoto o None = token invariato (il campo nel browser è in sola scrittura)."""
    url = ha.clean_url(url)
    new_token = (token or "").strip()
    if new_token:
        encrypted = box.encrypt(new_token)  # senza SETTINGS_KEY: NoSettingsKey
        set_setting(s, TOKEN_KEY, encrypted)
    old = get_config(s)
    _put_config(s, ha.HaConfig(url, old.calendars, old.sensors, old.shopping_list)
                if old else ha.HaConfig(url))


def save_choices(s: Session, calendars, sensors: list[ha.SensorChoice],
                 shopping_list: str | None) -> None:
    old = get_config(s)
    if old is None:
        return
    _put_config(s, ha.HaConfig(
        old.url, tuple(calendars),
        tuple(ha.SensorChoice(x.entity_id, _clean_label(x.label)) for x in sensors),
        shopping_list or None))


def clear(s: Session) -> None:
    for key in (CONFIG_KEY, TOKEN_KEY, SNAPSHOT_KEY, REFRESH_KEY):
        set_setting(s, key, None)


def get_token(s: Session, box: SecretBox) -> str | None:
    raw = get_setting(s, TOKEN_KEY, None)
    return box.decrypt(raw) if isinstance(raw, str) else None


def token_status(s: Session, box: SecretBox) -> str:
    """Cosa mostrare al posto del token: mai il token in chiaro."""
    raw = get_setting(s, TOKEN_KEY, None)
    if not isinstance(raw, str):
        return NO_KEY
    plain = box.decrypt(raw)
    return UNREADABLE_KEY if plain is None else mask_key(plain)


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


def _reading_from_json(data) -> ha.Reading:
    return ha.Reading(
        events=tuple(ha.Event(str(e["calendar"]), str(e["summary"]), str(e["start"]),
                              str(e["end"]), bool(e["all_day"])) for e in data["events"]),
        shopping=tuple(str(x) for x in data["shopping"]),
        sensors=tuple(ha.SensorValue(str(v["entity_id"]), str(v["label"]), str(v["state"]),
                                     v["unit"] if isinstance(v["unit"], str) else None)
                      for v in data["sensors"]),
        missing=tuple(str(x) for x in data["missing"]))


def get_snapshot(s: Session) -> Snapshot:
    """Mai un'eccezione: dati rotti valgono come nessuna lettura."""
    data = get_setting(s, SNAPSHOT_KEY, None)
    try:
        reading = _reading_from_json(data["reading"]) if data["reading"] is not None else None
        fetched = datetime.fromisoformat(data["fetched_at"]) if data["fetched_at"] else None
        error = data.get("error")
        error_at = datetime.fromisoformat(data["error_at"]) if data.get("error_at") else None
        return Snapshot(fetched, reading, str(error) if error else None, error_at)
    except (KeyError, TypeError, ValueError, AttributeError):
        return Snapshot(None, None)


def _put_snapshot(s: Session, snap: Snapshot) -> None:
    set_setting(s, SNAPSHOT_KEY, {
        "fetched_at": _iso(snap.fetched_at),
        "reading": asdict(snap.reading) if snap.reading else None,
        "error": snap.error, "error_at": _iso(snap.error_at)})


def save_reading(s: Session, reading: ha.Reading, now: datetime) -> None:
    _put_snapshot(s, Snapshot(now, reading))


def save_error(s: Session, error: str, now: datetime) -> None:
    old = get_snapshot(s)
    _put_snapshot(s, Snapshot(old.fetched_at, old.reading, error, now))


def request_refresh(s: Session, now: datetime) -> None:
    set_setting(s, REFRESH_KEY, now.isoformat())


def refresh_requested_after(s: Session, when: datetime | None) -> bool:
    raw = get_setting(s, REFRESH_KEY, None)
    try:
        requested = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return False
    if requested is None:
        return False
    return when is None or requested > when
