"""Provider e transport finti per i test dell'elaborazione AI: nessun test tocca la rete."""
import httpx
import pytest

from secondbrain.ai.audio import AudioSource
from secondbrain.ai.base import CheckResult, Enrichment, Transcript

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


DEFAULT_TEXT = "Devo chiamare Marco per il preventivo del tetto."
DEFAULT_ENRICHMENT = ("Chiamare Marco", "Chiamare Marco per il preventivo.", ("lavoro", "casa"))


def _next(queue: list, default):
    item = queue.pop(0) if queue else default
    if callable(item):  # azione da fare "durante" la chiamata (es. l'utente che corregge)
        item = item()
    if isinstance(item, BaseException):
        raise item
    return item


class FakeProvider:
    """Provider finto: risposte in coda (testo, tupla, eccezione o funzione), poi i default."""

    def __init__(self, name: str, transcripts=(), enrichments=()):
        self.name = name
        self.transcripts = list(transcripts)
        self.enrichments = list(enrichments)
        self.calls: list[tuple[str, str]] = []

    def transcribe(self, audio, language):
        self.calls.append(("transcribe", language))
        text = _next(self.transcripts, DEFAULT_TEXT)
        return Transcript(text=text, model=f"{self.name}-stt", raw={"text": text})

    def enrich(self, text, language):
        self.calls.append(("enrich", text))
        title, summary, tags = _next(self.enrichments, DEFAULT_ENRICHMENT)
        return Enrichment(title=title, summary=summary, tags=tuple(tags),
                          model=f"{self.name}-llm", raw={"title": title})

    def check(self):
        return CheckResult(True, "ok")


class FakeFactory:
    """Al posto di build_provider: restituisce il provider finto con quel nome."""

    def __init__(self, **providers: FakeProvider):
        self.providers = providers

    def __call__(self, config):
        return self.providers[config.name]
