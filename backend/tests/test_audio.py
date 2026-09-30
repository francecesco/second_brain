import numpy as np
import pytest
import soundfile as sf

from secondbrain.ai.audio import FLAC, MIME_TYPES, WAV, AudioError, AudioSource, wav_to_flac
from secondbrain.ai.registry import PROVIDERS
from tests.helpers import make_wav

RATE = 16000


def test_flac_keeps_duration_and_every_sample(tmp_path):
    src, dst = tmp_path / "nota.wav", tmp_path / "nota.flac"
    samples = (np.arange(RATE * 2) % 200 - 100).astype("<i2")
    sf.write(str(src), samples, RATE, subtype="PCM_16")
    wav_to_flac(src, dst)
    info = sf.info(str(dst))
    assert (info.format, info.samplerate, info.frames) == ("FLAC", RATE, len(samples))
    back, _ = sf.read(str(dst), dtype="int16")
    assert np.array_equal(back, samples)
    assert dst.stat().st_size < src.stat().st_size


def test_device_wav_becomes_flac_once(tmp_path):
    wav = tmp_path / "091530_dev.wav"
    wav.write_bytes(make_wav(1.0))
    audio = AudioSource(wav, tmp_path)
    path, mime = audio.prepare((FLAC, WAV))
    assert mime == "audio/flac" and path.read_bytes()[:4] == b"fLaC"
    assert audio.prepare((FLAC, WAV)) == (path, mime)
    assert audio.duration_s == pytest.approx(1.0)


def test_wav_only_provider_gets_the_wav(tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(make_wav(1.0))
    assert AudioSource(wav, tmp_path).prepare((WAV,)) == (wav, "audio/wav")


def test_samples_flac_would_change_fall_back_to_wav(tmp_path):
    wav = tmp_path / "otto_bit.wav"
    sf.write(str(wav), np.zeros(RATE, dtype="int16"), RATE, subtype="PCM_U8")
    audio = AudioSource(wav, tmp_path)
    assert audio.prepare((FLAC, WAV)) == (wav, "audio/wav")
    with pytest.raises(AudioError):
        audio.prepare((FLAC,))


def test_unreadable_audio(tmp_path):
    bad = tmp_path / "rotto.wav"
    bad.write_bytes(b"RIFFxxxxWAVEjunk")
    with pytest.raises(AudioError, match="illeggibile"):
        AudioSource(bad, tmp_path).duration_s
    with pytest.raises(AudioError, match="illeggibile"):
        AudioSource(bad, tmp_path).prepare((FLAC, WAV))


def test_every_provider_format_is_known():
    assert all(fmt in MIME_TYPES for spec in PROVIDERS for fmt in spec.audio_formats)


def test_missing_audio_is_not_unreadable(tmp_path):
    """Final review 3: un WAV sparito (spostato nel frattempo) non è un errore di contenuto."""
    gone = tmp_path / "spostato.wav"
    with pytest.raises(FileNotFoundError):
        AudioSource(gone, tmp_path).duration_s
    with pytest.raises(FileNotFoundError):
        AudioSource(gone, tmp_path).prepare((FLAC, WAV))
    with pytest.raises(FileNotFoundError):
        wav_to_flac(gone, tmp_path / "x.flac")
