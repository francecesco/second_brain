"""Audio verso i provider (spec §4): il WAV dell'archivio convertito in FLAC senza perdita.

Il FLAC pesa circa la metà (una nota da 10 minuti passa da ~19 MB a ~10 MB) e resta un
file temporaneo; in archivio resta il WAV. Ogni adattatore dice quali formati accetta.
"""
from collections.abc import Sequence
from pathlib import Path

import soundfile as sf

FLAC = "flac"
WAV = "wav"
MIME_TYPES = {FLAC: "audio/flac", WAV: "audio/wav"}
FLAC_SUBTYPES = ("PCM_16", "PCM_24")  # campioni che il FLAC conserva identici
BLOCK_FRAMES = 65536  # conversione a blocchi: mai l'intera nota in memoria


class AudioError(ValueError):
    """Audio illeggibile o in un formato che nessun adattatore accetta."""


class NotConvertible(AudioError):
    """WAV leggibile ma con campioni che il FLAC non conserverebbe identici."""


def wav_to_flac(src: Path, dst: Path) -> None:
    try:
        with sf.SoundFile(str(src)) as wav:
            if wav.subtype not in FLAC_SUBTYPES:
                raise NotConvertible(f"campioni {wav.subtype}: niente FLAC")
            with sf.SoundFile(str(dst), "w", samplerate=wav.samplerate, channels=wav.channels,
                              format="FLAC", subtype=wav.subtype) as flac:
                for block in wav.blocks(blocksize=BLOCK_FRAMES, dtype="int32"):
                    flac.write(block)
    except sf.SoundFileError as exc:
        raise AudioError(f"audio illeggibile: {exc}") from None


class AudioSource:
    """Il WAV di una nota, con il FLAC creato una sola volta in `workdir` se serve."""

    def __init__(self, wav_path: Path, workdir: Path):
        self.wav_path = wav_path
        self._workdir = workdir
        self._flac: Path | None = None
        self._duration: float | None = None

    @property
    def duration_s(self) -> float:
        if self._duration is None:
            try:
                self._duration = float(sf.info(str(self.wav_path)).duration)
            except sf.SoundFileError as exc:
                raise AudioError(f"audio illeggibile: {exc}") from None
        return self._duration

    def _flac_path(self) -> Path:
        if self._flac is None:
            dst = self._workdir / f"{self.wav_path.stem}.{FLAC}"
            wav_to_flac(self.wav_path, dst)
            self._flac = dst
        return self._flac

    def prepare(self, formats: Sequence[str]) -> tuple[Path, str]:
        """(file, tipo MIME) nel primo dei formati accettati che si riesce a produrre."""
        for fmt in formats:
            if fmt == FLAC:
                try:
                    return self._flac_path(), MIME_TYPES[FLAC]
                except NotConvertible:
                    continue
            if fmt == WAV:
                return self.wav_path, MIME_TYPES[WAV]
        raise AudioError(f"nessun formato accettato tra: {', '.join(formats)}")
