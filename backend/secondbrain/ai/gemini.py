"""Adattatore Gemini (spec §4): audio inline a `generateContent`, poi una seconda chiamata
di solo testo per l'arricchimento. La chiave va nell'header `x-goog-api-key`, mai nell'URL.
"""
import base64
import json

import httpx

from .audio import AudioSource
from .base import (CHECK_TIMEOUT, ENRICH_TIMEOUT, OK, TRANSCRIBE_TIMEOUT, CheckResult,
                   ContentError, Enrichment, ProviderBase, ProviderError, ServiceError,
                   Transcript, check_request_size, enrich_prompt, error_message, http_detail,
                   json_body, parse_enrichment, prepare_audio, run_check, transcribe_prompt)

AUDIO_CONTENT_STATUSES = (400, 413)
# Gemini risponde 400 INVALID_ARGUMENT anche a una chiave sbagliata: non è colpa
# dell'audio, si passa alla riserva (Review Focus 2).
KEY_ERROR_REASONS = ("API_KEY_INVALID", "API_KEY_EXPIRED")
KEY_ERROR_HINT = "api key"
BLOCKED_FINISH_REASONS = ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII")
LIST_PAGE_SIZE = 1000
MODEL_PREFIX = "models/"
ENRICH_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "summary", "tags"],
}


def _reasons(response: httpx.Response) -> set[str]:
    try:
        error = response.json().get("error") or {}
        details = error.get("details") or []
        return {d.get("reason") for d in details if isinstance(d, dict)} - {None}
    except (ValueError, AttributeError):
        return set()


class Gemini(ProviderBase):
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key}

    def _error(self, response: httpx.Response, *, audio: bool) -> ProviderError:
        detail = http_detail(response, (self._key,))
        key_problem = (bool(_reasons(response) & set(KEY_ERROR_REASONS))
                       or KEY_ERROR_HINT in error_message(response).lower())
        if audio and response.status_code in AUDIO_CONTENT_STATUSES and not key_problem:
            return ContentError(self.name, detail)
        return ServiceError(self.name, detail)

    def _generate(self, model: str, payload: dict, timeout: httpx.Timeout, *,
                  audio: bool) -> dict:
        body = json.dumps(payload).encode()
        check_request_size(self.name, len(body), self.max_bytes, what="richiesta")
        response = self._send("POST", f"/models/{model}:generateContent", content=body,
                              headers={"Content-Type": "application/json"}, timeout=timeout)
        if response.status_code != OK:
            raise self._error(response, audio=audio)
        return json_body(self.name, response)

    def _text(self, body: dict, *, audio: bool) -> str:
        blocked = ContentError if audio else ServiceError
        candidates = body.get("candidates") or []
        if not candidates:
            reason = (body.get("promptFeedback") or {}).get("blockReason", "nessuna risposta")
            raise blocked(self.name, f"risposta bloccata: {reason}")
        first = candidates[0]
        finish = first.get("finishReason")
        if finish in BLOCKED_FINISH_REASONS:
            raise blocked(self.name, f"risposta interrotta: {finish}")
        parts = (first.get("content") or {}).get("parts") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")]
        if not texts:
            # Candidato senza parti di testo (`content: {}` con STOP, oppure solo "thoughts"
            # e MAX_TOKENS): non e' silenzio, e' il servizio che non ha risposto. Il silenzio
            # vero arriva come parte con testo vuoto.
            raise ServiceError(self.name, f"risposta senza testo (finishReason: {finish})")
        return "".join(texts)

    def transcribe(self, audio: AudioSource, language: str) -> Transcript:
        path, mime = prepare_audio(self.name, audio, self.audio_formats)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {"contents": [{"role": "user", "parts": [
            {"text": transcribe_prompt(language)},
            {"inline_data": {"mime_type": mime, "data": data}},
        ]}]}
        body = self._generate(self.transcribe_model, payload, TRANSCRIBE_TIMEOUT, audio=True)
        return Transcript(text=self._text(body, audio=True).strip(), model=self.transcribe_model,
                          raw=body)

    def enrich(self, text: str, language: str) -> Enrichment:
        if not self.text_model:
            raise ServiceError(self.name, "modello di testo non impostato")
        payload = {
            "systemInstruction": {"parts": [{"text": enrich_prompt(language)}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": ENRICH_SCHEMA},
        }
        body = self._generate(self.text_model, payload, ENRICH_TIMEOUT, audio=False)
        return parse_enrichment(self.name, self.text_model, self._text(body, audio=False), body)

    def check(self) -> CheckResult:
        def extract_names(body: dict) -> set[str]:
            models = body.get("models")
            return {str(m.get("name", "")).removeprefix(MODEL_PREFIX)
                   for m in (models if isinstance(models, list) else []) if isinstance(m, dict)}

        return run_check(
            self.name, (self._key,),
            lambda: self._send("GET", "/models", params={"pageSize": LIST_PAGE_SIZE},
                              timeout=CHECK_TIMEOUT),
            extract_names, (self.transcribe_model, self.text_model))
