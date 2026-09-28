"""Utilità condivise dai test."""
import os
import struct

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sb:sb@localhost:55432/sb_test"
)


def make_wav(seconds: float = 1.0, rate: int = 16000, fill: bytes = b"\x01\x00",
             audio_format: int = 1, extra_chunk: bytes = b"",
             declared_data: int | None = None) -> bytes:
    """WAV mono 16 bit come quelli del device; `fill` (2 byte) cambia il contenuto."""
    data = fill * int(seconds * rate)
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, audio_format, 1, rate, rate * 2, 2, 16)
    size = len(data) if declared_data is None else declared_data
    body = b"WAVE" + extra_chunk + fmt + b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body
