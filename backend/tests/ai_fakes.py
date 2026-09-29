"""Provider e transport finti per i test dell'elaborazione AI: nessun test tocca la rete."""
import httpx


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
