"""Interfacce dei provider, risultati, errori, ripulitura dei messaggi, prompt (spec §4, §5, §8, §9).

Due famiglie di errori decidono cosa fa la catena (spec §5):
- ServiceError: problema del servizio (timeout, rete, 5xx, 429, 401/403, risposta fuori
  schema) → si prova il provider successivo;
- ContentError: problema dell'audio (400/422 sull'audio, oltre il limite) → la nota
  fallisce subito, nessun altro provider.
Ogni messaggio passa da `redact` prima di finire in un'eccezione: niente chiavi, niente
header, niente corpo della richiesta, lunghezza limitata.
"""
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..languages import language_name
from ..notefile import (MAX_SUMMARY_LEN, MAX_TAGS, MAX_TITLE_LEN, EnrichmentInvalid,
                        validate_enrichment)
from .audio import AudioError, AudioSource

OK = 200
MAX_ERROR_LEN = 300
REDACTED = "***"
KEY_PATTERNS = (
    re.compile(r"gsk_[A-Za-z0-9_]{8,}"),                   # Groq (anche le chiavi finte *_test_*)
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),                 # OpenAI; \b esclude "disk-..."
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),                 # Google (anche AIza-test-...)
    re.compile(r"(?i)\b(?:key|api_key|token)=[^&\s\"']+"),  # chiave finita in un URL
)
CONNECT_TIMEOUT_S = 10.0
TRANSCRIBE_TIMEOUT = httpx.Timeout(300.0, connect=CONNECT_TIMEOUT_S)  # 10 min di audio
ENRICH_TIMEOUT = httpx.Timeout(120.0, connect=CONNECT_TIMEOUT_S)
CHECK_TIMEOUT = httpx.Timeout(15.0, connect=CONNECT_TIMEOUT_S)
CODE_FENCE = "```"


class ProviderError(Exception):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.detail = detail


class ServiceError(ProviderError):
    """Il servizio non ha risposto come serve: si passa al provider successivo."""


class ContentError(ProviderError):
    """L'audio non va bene per il provider: la nota fallisce subito."""


@dataclass(frozen=True)
class Transcript:
    text: str
    model: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class Enrichment:
    title: str
    summary: str | None
    tags: tuple[str, ...]
    model: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


class Transcriber(Protocol):
    def transcribe(self, audio: AudioSource, language: str) -> Transcript: ...


class Enricher(Protocol):
    def enrich(self, text: str, language: str) -> Enrichment: ...


class Provider(Transcriber, Enricher, Protocol):
    name: str

    def check(self) -> CheckResult: ...


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    for pattern in KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = " ".join(text.split())
    return text if len(text) <= MAX_ERROR_LEN else text[:MAX_ERROR_LEN - 1] + "…"


def _error_field(response: httpx.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None


def error_message(response: httpx.Response) -> str:
    error = _error_field(response)
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return response.text


def error_code(response: httpx.Response) -> str | None:
    error = _error_field(response)
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def http_detail(response: httpx.Response, secrets: Iterable[str]) -> str:
    return redact(f"HTTP {response.status_code}: {error_message(response)}", secrets)


def send(client: httpx.Client, method: str, url: str, *, provider: str,
         secrets: Iterable[str], **kwargs: Any) -> httpx.Response:
    """Una richiesta; timeout ed errori di rete diventano ServiceError ripuliti."""
    secrets = tuple(secrets)
    try:
        return client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise ServiceError(provider, "timeout") from None
    except httpx.HTTPError as exc:
        raise ServiceError(provider, redact(f"errore di rete ({type(exc).__name__}): {exc}",
                                            secrets)) from None


class ProviderBase:
    """Campi comuni e invio HTTP dei due adattatori (OpenAI-compatibile e Gemini, spec §4).

    Ogni sottoclasse implementa solo `_headers` (dove va la chiave) e la classificazione
    degli errori: il resto (campi del costruttore, preparazione dell'audio, invio della
    richiesta, struttura di "Prova") è qui.
    """

    def __init__(self, name: str, *, api_key: str, base_url: str, transcribe_model: str,
                text_model: str, audio_formats: tuple[str, ...], max_bytes: int,
                client: httpx.Client):
        self.name = name
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.transcribe_model = transcribe_model
        self.text_model = text_model
        self.audio_formats = audio_formats
        self.max_bytes = max_bytes
        self._client = client

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = self._headers() | kwargs.pop("headers", {})
        return send(self._client, method, f"{self.base_url}{path}", provider=self.name,
                   secrets=(self._key,), headers=headers, **kwargs)


def prepare_audio(provider: str, audio: AudioSource,
                  formats: Sequence[str]) -> tuple[Path, str]:
    """(file, tipo MIME) nel primo formato accettato che si riesce a produrre.

    Un audio illeggibile o senza formato accettato è un problema dell'audio, non del
    servizio: `ContentError`, non `AudioError`.
    """
    try:
        return audio.prepare(formats)
    except AudioError as exc:
        raise ContentError(provider, str(exc)) from None


def check_request_size(provider: str, size: int, max_bytes: int, *, what: str) -> None:
    """Solleva `ContentError` (nessuna richiesta di rete) se `size` supera `max_bytes`."""
    if size > max_bytes:
        raise ContentError(provider, f"{what} di {size} byte oltre il limite di {max_bytes}")


def json_body(provider: str, response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise ServiceError(provider, "risposta non JSON") from None
    if not isinstance(body, dict):
        raise ServiceError(provider, "risposta JSON inattesa")
    return body


def enrich_prompt(language: str) -> str:
    return (
        "Ricevi la trascrizione di una nota vocale. Rispondi solo con un oggetto JSON con "
        'tre chiavi: "title" (titolo breve su una riga, al massimo '
        f'{MAX_TITLE_LEN} caratteri), "summary" (riassunto di una o due frasi, al massimo '
        f'{MAX_SUMMARY_LEN} caratteri) e "tags" (elenco da 1 a {MAX_TAGS} parole chiave '
        f"minuscole, senza #). Scrivi titolo, riassunto e tag in {language_name(language)}."
    )


def transcribe_prompt(language: str) -> str:
    return (
        f"Trascrivi fedelmente il parlato di questo audio in {language_name(language)}. "
        "Rispondi solo con il testo trascritto, senza commenti, titoli o marcatori di tempo. "
        "Se non c'è parlato rispondi con una stringa vuota."
    )


def _strip_fence(content: str) -> str:
    text = content.strip()
    if text.startswith(CODE_FENCE):
        text = text.removeprefix(CODE_FENCE).removeprefix("json").removesuffix(CODE_FENCE)
    return text.strip()


def parse_enrichment(provider: str, model: str, content: object,
                     raw: dict[str, Any]) -> Enrichment:
    """Testo JSON del modello → Enrichment; fuori schema è un ServiceError (spec §9)."""
    if not isinstance(content, str):
        raise ServiceError(provider, "arricchimento: risposta senza testo")
    try:
        data = json.loads(_strip_fence(content))
    except ValueError:
        raise ServiceError(provider, "arricchimento: risposta non JSON") from None
    try:
        title, summary, tags = validate_enrichment(data)
    except EnrichmentInvalid as exc:
        raise ServiceError(provider, f"arricchimento fuori schema: {exc}") from None
    return Enrichment(title=title, summary=summary, tags=tags, model=model, raw=raw)


def check_models(available: set[str], wanted: Iterable[str]) -> CheckResult:
    """Esito di "Prova": la chiave funziona e i modelli impostati esistono?"""
    wanted = [model for model in wanted if model]
    missing = [model for model in wanted if model not in available]
    if missing:
        return CheckResult(False, f"Chiave valida, ma modelli non trovati: {', '.join(missing)}")
    if not wanted:
        return CheckResult(True, "Chiave valida")
    return CheckResult(True, f"Chiave valida; modelli disponibili: {', '.join(wanted)}")


def run_check(provider: str, secrets: Iterable[str], request: Callable[[], httpx.Response],
             extract_names: Callable[[dict[str, Any]], set[str]],
             wanted: Iterable[str]) -> CheckResult:
    """Struttura comune di "Prova": una richiesta, un errore non-200 o di rete diventa
    `CheckResult(False, ...)`, altrimenti si confrontano i modelli voluti con quelli
    elencati dal provider (`extract_names` sa leggere la forma della sua risposta).
    """
    try:
        response = request()
        if response.status_code != OK:
            return CheckResult(False, http_detail(response, secrets))
        body = json_body(provider, response)
    except ServiceError as exc:
        return CheckResult(False, exc.detail)
    return check_models(extract_names(body), wanted)
