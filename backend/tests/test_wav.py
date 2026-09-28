import struct

import pytest

from secondbrain.wav import WavError, parse_wav_header
from tests.helpers import make_wav


def parse(blob: bytes):
    return parse_wav_header(blob[:4096], len(blob))


def test_device_wav():
    info = parse(make_wav(1.5))
    assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)
    assert info.data_bytes == 48000
    assert info.duration_s == pytest.approx(1.5)


def test_skips_unknown_chunks_with_odd_padding():
    extra = b"LIST" + struct.pack("<I", 3) + b"abc" + b"\x00"
    assert parse(make_wav(1.0, extra_chunk=extra)).duration_s == pytest.approx(1.0)


def test_truncated_file_uses_available_bytes():
    blob = make_wav(1.0)[:-3200]  # manca 0,1 s
    assert parse(blob).duration_s == pytest.approx(0.9)


def test_declared_size_larger_than_file():
    assert parse(make_wav(1.0, declared_data=0xFFFFFFFF)).duration_s == pytest.approx(1.0)


@pytest.mark.parametrize("blob,msg", [
    (b"", "RIFF"),
    (b"OggS" + b"\x00" * 60, "RIFF"),
    (make_wav(1.0, audio_format=3), "PCM"),
    (make_wav(0.0), "campione"),
    (b"RIFF\x04\x00\x00\x00WAVE", "data"),
    (b"RIFF\x0c\x00\x00\x00WAVEdata\x00\x00\x00\x00", "fmt"),
])
def test_invalid(blob, msg):
    with pytest.raises(WavError, match=msg):
        parse(blob)
