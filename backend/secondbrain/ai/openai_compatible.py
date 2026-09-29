"""Adattatore per le API compatibili OpenAI: Groq e OpenAI (spec §4).

Stesse API `/audio/transcriptions` e `/chat/completions`; cambiano URL base, modelli e
formati audio accettati. La chiave va nell'header Authorization.
"""
import httpx

from .audio import AudioError, AudioSource
from .base import (CHECK_TIMEOUT, ENRICH_TIMEOUT, TRANSCRIBE_TIMEOUT, CheckResult, ContentError,
                   Enrichment, ProviderError, ServiceError, Transcript, check_models,
                   enrich_prompt, error_code, http_detail, json_body, parse_enrichment, send)

# Sull'audio questi codici dicono "il file non va bene": nessun altro provider.
AUDIO_CONTENT_STATUSES = (400, 413, 415, 422)
# ...tranne quando il corpo dice che il problema è la chiave o il modello.
SERVICE_ERROR_CODES = ("invalid_api_key", "model_not_found", "model_decommissioned")
OK = 200


class OpenAICompatible:
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
        return {"Authorization": f"Bearer {self._key}"}

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        return send(self._client, method, f"{self.base_url}{path}", provider=self.name,
                    secrets=(self._key,), headers=self._headers(), **kwargs)

    def _error(self, response: httpx.Response, *, audio: bool) -> ProviderError:
        detail = http_detail(response, (self._key,))
        if (audio and response.status_code in AUDIO_CONTENT_STATUSES
                and error_code(response) not in SERVICE_ERROR_CODES):
            return ContentError(self.name, detail)
        return ServiceError(self.name, detail)

    def transcribe(self, audio: AudioSource, language: str) -> Transcript:
        try:
            path, mime = audio.prepare(self.audio_formats)
        except AudioError as exc:
            raise ContentError(self.name, str(exc)) from None
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ContentError(self.name, f"audio di {size} byte oltre il limite di "
                                          f"{self.max_bytes}")
        with open(path, "rb") as f:
            response = self._send(
                "POST", "/audio/transcriptions", timeout=TRANSCRIBE_TIMEOUT,
                data={"model": self.transcribe_model, "language": language,
                      "response_format": "json"},
                files={"file": (path.name, f, mime)})
        if response.status_code != OK:
            raise self._error(response, audio=True)
        body = json_body(self.name, response)
        text = body.get("text")
        if not isinstance(text, str):
            raise ServiceError(self.name, "trascrizione: risposta senza testo")
        return Transcript(text=text.strip(), model=self.transcribe_model, raw=body)

    def enrich(self, text: str, language: str) -> Enrichment:
        if not self.text_model:
            raise ServiceError(self.name, "modello di testo non impostato")
        payload = {
            "model": self.text_model,
            "messages": [{"role": "system", "content": enrich_prompt(language)},
                         {"role": "user", "content": text}],
            "response_format": {"type": "json_object"},
        }
        response = self._send("POST", "/chat/completions", timeout=ENRICH_TIMEOUT, json=payload)
        if response.status_code != OK:
            raise self._error(response, audio=False)
        body = json_body(self.name, response)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ServiceError(self.name, "arricchimento: risposta senza contenuto") from None
        return parse_enrichment(self.name, self.text_model, content, body)

    def check(self) -> CheckResult:
        """"Prova": elenco dei modelli, chiamata gratuita che verifica anche la chiave."""
        try:
            response = self._send("GET", "/models", timeout=CHECK_TIMEOUT)
            if response.status_code != OK:
                return CheckResult(False, self._error(response, audio=False).detail)
            body = json_body(self.name, response)
        except ServiceError as exc:
            return CheckResult(False, exc.detail)
        data = body.get("data")
        models = data if isinstance(data, list) else []
        ids = {str(m.get("id")) for m in models if isinstance(m, dict)}
        return check_models(ids, (self.transcribe_model, self.text_model))
