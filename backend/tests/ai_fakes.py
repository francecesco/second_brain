"""Provider e transport finti per i test dell'elaborazione AI: nessun test tocca la rete."""
import httpx
import pytest

from secondbrain.ai.audio import AudioSource

from .helpers import make_wav


@pytest.fixture
def audio(tmp_path):
    """Un `AudioSource` di 1 s pronto per un adattatore, come lo vede il worker."""
    wav = tmp_path / "091530_70041dd8263c.wav"
    wav.write_bytes(make_wav(1.0))
    return AudioSource(wav, tmp_path)


class Recorder:
    """Transport finto: risponde in ordine con Response, o solleva la classe d'eccezione data."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, type) and issubclass(item, Exception):
            raise item("simulato", request=request)
        return item
