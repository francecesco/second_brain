"""Utilità condivise dai test."""
import os
import struct
import uuid
from datetime import UTC, date, datetime

from secondbrain.models import Capture, Device

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sb:sb@localhost:55432/sb_test"
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DEV = "70041dd8263c"
DEV2 = "aabbccddeeff"


def make_wav(seconds: float = 1.0, rate: int = 16000, fill: bytes = b"\x01\x00",
             audio_format: int = 1, extra_chunk: bytes = b"",
             declared_data: int | None = None) -> bytes:
    """WAV mono 16 bit come quelli del device; `fill` (2 byte) cambia il contenuto."""
    data = fill * int(seconds * rate)
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, audio_format, 1, rate, rate * 2, 2, 16)
    size = len(data) if declared_data is None else declared_data
    body = b"WAVE" + extra_chunk + fmt + b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_device(**over) -> Device:
    fields = dict(id=DEV, name="e-paper", type="epaper154", token_hash=None, created_at=NOW)
    fields.update(over)
    return Device(**fields)


def make_capture(**over) -> Capture:
    fields = dict(
        id=uuid.uuid4(), device_id=DEV, capture_id="cap_20260923_191530",
        recorded_at=datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC), date_estimated=False,
        received_at=NOW, day=date(2026, 9, 23),
        rel_path="2026/09/23/211530_70041dd8263c.wav", title=None, duration_s=1.0,
        size_bytes=32044, sha256="a" * 64, firmware_version="0.6.2", battery_pct=None,
        battery_v=4.1, power_source="usb", trashed_at=None,
    )
    fields.update(over)
    return Capture(**fields)
