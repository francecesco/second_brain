"""Adattatore per le API compatibili OpenAI: Groq e OpenAI (spec §4).

Stesse API `/audio/transcriptions` e `/chat/completions`; cambiano URL base, modelli e
formati audio accettati. La chiave va nell'header Authorization.
"""
import httpx

from .audio import AudioSource
from .base import (CHECK_TIMEOUT, ENRICH_TIMEOUT, OK, TRANSCRIBE_TIMEOUT, CheckResult,
                   ContentError, Enrichment, ProviderBase, ProviderError, ServiceError,
                   Transcript, check_request_size, enrich_prompt, error_code, http_detail,
                   json_body, parse_enrichment, prepare_audio, run_check)

# Sull'audio questi codici dicono "il file non va bene": nessun altro provider.
AUDIO_CONTENT_STATUSES = (400, 413, 415, 422)
# ...tranne quando il corpo dice che il problema è la chiave o il modello.
SERVICE_ERROR_CODES = ("invalid_api_key", "model_not_found", "model_decommissioned")


class OpenAICompatible(ProviderBase):
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def _error(self, response: httpx.Response, *, audio: bool) -> ProviderError:
        detail = http_detail(response, (self._key,))
        if (audio and response.status_code in AUDIO_CONTENT_STATUSES
                and error_code(response) not in SERVICE_ERROR_CODES):
            return ContentError(self.name, detail)
        return ServiceError(self.name, detail)

    def transcribe(self, audio: AudioSource, language: str) -> Transcript:
        path, mime = prepare_audio(self.name, audio, self.audio_formats)
        check_request_size(self.name, path.stat().st_size, self.max_bytes, what="audio")
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
        def extract_names(body: dict) -> set[str]:
            data = body.get("data")
            models = data if isinstance(data, list) else []
            return {str(m.get("id")) for m in models if isinstance(m, dict)}

        return run_check(self.name, (self._key,),
                         lambda: self._send("GET", "/models", timeout=CHECK_TIMEOUT),
                         extract_names, (self.transcribe_model, self.text_model))
