"""Validazione dell'header WAV ricevuto (spec §6 punto 4): funzione pura."""
import struct
from dataclasses import dataclass

HEADER_PROBE_BYTES = 4096
PCM = 1
SUPPORTED_BITS = (8, 16, 24, 32)


class WavError(ValueError):
    """Il file non è un WAV PCM utilizzabile."""


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    data_bytes: int

    @property
    def duration_s(self) -> float:
        bytes_per_second = self.sample_rate * self.channels * self.bits_per_sample // 8
        return self.data_bytes / bytes_per_second


def parse_wav_header(head: bytes, total_size: int) -> WavInfo:
    """`head`: i primi byte del file (fino a HEADER_PROBE_BYTES); `total_size`: byte totali."""
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise WavError("non è un file RIFF/WAVE")
    fmt: tuple[int, int, int] | None = None
    pos = 12
    while pos + 8 <= len(head):
        chunk_id = head[pos:pos + 4]
        (size,) = struct.unpack_from("<I", head, pos + 4)
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(head):
                raise WavError("chunk fmt troncato")
            audio_format, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", head, body)
            if audio_format != PCM:
                raise WavError(f"formato {audio_format} non PCM")
            if channels == 0 or rate == 0 or bits not in SUPPORTED_BITS:
                raise WavError("parametri del chunk fmt non validi")
            fmt = (rate, channels, bits)
        elif chunk_id == b"data":
            if fmt is None:
                raise WavError("chunk data prima del chunk fmt")
            data_bytes = min(size, max(total_size - body, 0))
            if data_bytes == 0:
                raise WavError("nessun campione audio")
            return WavInfo(fmt[0], fmt[1], fmt[2], data_bytes)
        pos = body + size + (size & 1)
    raise WavError("chunk data non trovato")
