import base64
import json
import logging

import httpx
import pytest

from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.gemini import Gemini
from secondbrain.ai.openai_compatible import OpenAICompatible
from secondbrain.ai.registry import PROVIDER_SPECS, ProviderConfig, build_provider
from tests.ai_fakes import Recorder, audio  # noqa: F401 - fixture riusata

KEY = "AIza-test-gemini-0123456789abcdefghij"
GENERATE_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.8-flash:generateContent")
BAD_KEY = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                     "status": "INVALID_ARGUMENT",
                     "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                  "reason": "API_KEY_INVALID", "domain": "googleapis.com"}]}}


def make(recorder, **over):
    spec = PROVIDER_SPECS["gemini"]
    fields = dict(api_key=KEY, base_url=spec.base_url, transcribe_model=spec.transcribe_model,
                  text_model=spec.text_model, audio_formats=spec.audio_formats,
                  max_bytes=spec.max_bytes)
    fields.update(over)
    return Gemini("gemini", client=httpx.Client(transport=httpx.MockTransport(recorder)), **fields)


def answer(*texts, finish="STOP"):
    parts = [{"text": t} for t in texts]
    return httpx.Response(200, json={"candidates": [{"content": {"parts": parts, "role": "model"},
                                                     "finishReason": finish}]})


def test_transcription_request_and_parsing(audio):
    thought = {"candidates": [{"content": {"parts": [
        {"text": "sto pensando", "thought": True}, {"text": " Devo chiamare "},
        {"text": "Marco. "}]}, "finishReason": "STOP"}]}
    rec = Recorder(httpx.Response(200, json=thought))
    t = make(rec).transcribe(audio, "it")
    req = rec.requests[0]
    assert (req.method, str(req.url)) == ("POST", GENERATE_URL)
    assert req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(req.url) and "key=" not in str(req.url)
    parts = json.loads(req.content)["contents"][0]["parts"]
    assert "italiano" in parts[0]["text"]
    assert parts[1]["inline_data"]["mime_type"] == "audio/flac"
    assert base64.b64decode(parts[1]["inline_data"]["data"])[:4] == b"fLaC"
    assert (t.text, t.model) == ("Devo chiamare Marco.", "gemini-3.8-flash")


def test_request_over_the_inline_limit_is_a_content_error(audio):
    rec = Recorder()
    with pytest.raises(ContentError, match="oltre il limite"):
        make(rec, max_bytes=100).transcribe(audio, "it")
    assert rec.requests == []


def test_wrong_key_is_a_service_error_even_with_400(audio):
    with pytest.raises(ServiceError, match="HTTP 400"):
        make(Recorder(httpx.Response(400, json=BAD_KEY))).transcribe(audio, "it")


@pytest.mark.parametrize("status,error", [
    (400, ContentError), (413, ContentError), (429, ServiceError), (403, ServiceError),
    (500, ServiceError), (503, ServiceError),
])
def test_transcription_errors_are_classified(audio, status, error):
    body = {"error": {"code": status, "message": "Request contains an invalid argument.",
                      "status": "INVALID_ARGUMENT"}}
    with pytest.raises(error):
        make(Recorder(httpx.Response(status, json=body))).transcribe(audio, "it")


def test_blocked_audio_is_a_content_error(audio):
    blocked = httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ContentError, match="SAFETY"):
        make(Recorder(blocked)).transcribe(audio, "it")
    with pytest.raises(ContentError, match="RECITATION"):
        make(Recorder(answer("x", finish="RECITATION"))).transcribe(audio, "it")


def test_silence_is_an_empty_transcript(audio):
    t = make(Recorder(answer(""))).transcribe(audio, "it")
    assert t.text == ""


def test_candidate_without_parts_goes_to_the_next_provider(audio):
    # Visto il 2026-10-07 su un WAV gia' trascritto due volte: candidato con `content: {}`,
    # finishReason STOP e quasi 2000 token di "thoughts". Non e' silenzio: e' il servizio.
    empty = httpx.Response(200, json={"candidates": [{"content": {}, "finishReason": "STOP"}]})
    with pytest.raises(ServiceError, match="senza testo"):
        make(Recorder(empty)).transcribe(audio, "it")
    with pytest.raises(ServiceError, match="senza testo"):
        make(Recorder(empty)).enrich("testo", "it")
    truncated = httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "penso", "thought": True}]}, "finishReason": "MAX_TOKENS"}]})
    with pytest.raises(ServiceError, match="senza testo"):
        make(Recorder(truncated)).transcribe(audio, "it")


def test_enrich_request_and_parsing():
    rec = Recorder(answer(json.dumps({"title": "Chiamare Marco", "summary": "Entro venerdì.",
                                      "tags": ["Lavoro"]})))
    e = make(rec).enrich("Devo chiamare Marco", "it")
    payload = json.loads(rec.requests[0].content)
    assert "italiano" in payload["systemInstruction"]["parts"][0]["text"]
    assert payload["contents"][0]["parts"][0]["text"] == "Devo chiamare Marco"
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["title", "summary", "tags"]
    assert (e.title, e.summary, e.tags, e.model) == ("Chiamare Marco", "Entro venerdì.",
                                                     ("lavoro",), "gemini-3.8-flash")


@pytest.mark.parametrize("response", [
    answer("non JSON"), answer('{"title": "T"}'),
    httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
    httpx.Response(400, json={"error": {"message": "invalid"}}),
])
def test_bad_enrichment_goes_to_the_next_provider(response):
    with pytest.raises(ServiceError):
        make(Recorder(response)).enrich("testo", "it")


def test_check_lists_models_with_the_key_in_the_header():
    rec = Recorder(httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-flash"}]}))
    result = make(rec).check()
    req = rec.requests[0]
    assert result.ok and req.method == "GET"
    assert req.url.path == "/v1beta/models" and req.url.params["pageSize"] == "1000"
    assert req.headers["x-goog-api-key"] == KEY and KEY not in str(req.url)
    assert not make(Recorder(httpx.Response(200, json={"models": []}))).check().ok


def test_the_key_never_leaks(audio, caplog):
    caplog.set_level(logging.DEBUG)
    echo = {"error": {"code": 403, "message": f"key {KEY} blocked", "status": "PERMISSION_DENIED"}}
    rec = Recorder(httpx.Response(403, json=echo), httpx.Response(400, json=BAD_KEY))
    provider = make(rec)
    with pytest.raises(ServiceError) as exc:
        provider.transcribe(audio, "it")
    result = provider.check()
    for text in (str(exc.value), result.message, caplog.text):
        assert KEY not in text


def test_build_provider_picks_the_adapter():
    client = httpx.Client(transport=httpx.MockTransport(Recorder()))
    gemini = build_provider(ProviderConfig("gemini", "gemini-3.8-flash", "gemini-3.8-flash", KEY),
                            client)
    groq = build_provider(ProviderConfig("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b",
                                         "gsk_test_0123456789"), client)
    assert isinstance(gemini, Gemini) and gemini.base_url == PROVIDER_SPECS["gemini"].base_url
    assert isinstance(groq, OpenAICompatible) and groq.audio_formats == ("flac", "wav")
    assert groq.transcribe_model == "whisper-large-v3-turbo"
