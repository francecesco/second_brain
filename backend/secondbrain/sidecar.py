"""Sidecar JSON di una registrazione (spec §4): conversioni pure.

`rel_path` e `day` non ci sono: si ricavano dalla posizione del file.
"""
import uuid
from datetime import datetime

SCHEMA_VERSION = 1
FIELDS = (
    "id", "device_id", "capture_id", "recorded_at", "date_estimated", "received_at",
    "title", "duration_s", "size_bytes", "sha256", "firmware_version", "battery_pct",
    "battery_v", "power_source", "trashed_at",
)
DATETIME_FIELDS = ("recorded_at", "received_at", "trashed_at")
REQUIRED = ("id", "device_id", "capture_id", "recorded_at", "received_at",
            "duration_s", "size_bytes", "sha256")


def capture_to_sidecar(capture) -> dict:
    data: dict = {"schema_version": SCHEMA_VERSION}
    for name in FIELDS:
        value = getattr(capture, name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, uuid.UUID):
            value = str(value)
        data[name] = value
    return data


def sidecar_to_fields(data: dict) -> dict:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version non supportata: {data.get('schema_version')!r}")
    missing = [name for name in REQUIRED if data.get(name) is None]
    if missing:
        raise ValueError(f"campi obbligatori mancanti: {', '.join(missing)}")
    fields = {name: data.get(name) for name in FIELDS}
    fields["id"] = uuid.UUID(fields["id"])
    for name in DATETIME_FIELDS:
        if fields[name] is not None:
            fields[name] = datetime.fromisoformat(fields[name])
    fields["date_estimated"] = bool(fields["date_estimated"])
    return fields
