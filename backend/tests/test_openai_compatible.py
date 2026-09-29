import json
import logging

import httpx
import pytest

from secondbrain.ai.audio import AudioSource
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.openai_compatible import OpenAICompatible
from secondbrain.ai.registry import PROVIDER_SPECS
from tests.ai_fakes import Recorder
from tests.helpers import make_wav

KEY = "gsk_test_secret_0123456789abcdef"
TEXT_MODEL_FOR_TESTS = "gpt-test"


def make(name, recorder, **over):
    spec = PROVIDER_SPECS[name]
    fields = dict(api_key=KEY, base_url=spec.base_url, transcribe_model=spec.transcribe_model,
                  text_model=spec.text_model or TEXT_MODEL_FOR_TESTS,
                  audio_formats=spec.audio_formats, max_bytes=spec.max_bytes)
    fields.update(over)
    return OpenAICompatible(name, client=httpx.Client(transport=httpx.MockTransport(recorder)),
                            **fields)


@pytest.fixture
def audio(tmp_path):
    wav = tmp_path / "091530_70041dd8263c.wav"
    wav.write_bytes(make_wav(1.0))
    return AudioSource(wav, tmp_path)


def chat(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def field(name, value):
    return f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()


def test_groq_transcription_request_and_parsing(audio):
    rec = Recorder(httpx.Response(200, json={"text": " Devo chiamare Marco. "}))
    t = make("groq", rec).transcribe(audio, "it")
    req = rec.requests[0]
    assert (req.method, str(req.url)) == ("POST", "https://api.groq.com/openai/v1/audio/transcriptions")
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = req.content
    assert field("model", "whisper-large-v3-turbo") in body
    assert field("language", "it") in body and field("response_format", "json") in body
    assert b'filename="091530_70041dd8263c.flac"' in body and b"fLaC" in body
    assert (t.text, t.model, t.raw) == ("Devo chiamare Marco.", "whisper-large-v3-turbo",
                                        {"text": " Devo chiamare Marco. "})


def test_openai_gets_the_wav(audio):
    rec = Recorder(httpx.Response(200, json={"text": "ciao"}))
    make("openai", rec).transcribe(audio, "it")
    body = rec.requests[0].content
    assert b'filename="091530_70041dd8263c.wav"' in body and b"WAVE" in body and b"fLaC" not in body
    assert str(rec.requests[0].url) == "https://api.openai.com/v1/audio/transcriptions"


def test_audio_over_the_limit_is_a_content_error_without_calling(audio):
    rec = Recorder()
    with pytest.raises(ContentError, match="oltre il limite"):
        make("groq", rec, max_bytes=10).transcribe(audio, "it")
    assert rec.requests == []


@pytest.mark.parametrize("status,error", [
    (429, ServiceError), (401, ServiceError), (403, ServiceError), (500, ServiceError),
    (503, ServiceError), (400, ContentError), (413, ContentError), (422, ContentError),
])
def test_transcription_errors_are_classified(audio, status, error):
    rec = Recorder(httpx.Response(status, json={"error": {"message": f"errore {status}"}}))
    with pytest.raises(error, match=f"HTTP {status}"):
        make("groq", rec).transcribe(audio, "it")


def test_bad_model_on_the_audio_call_is_a_service_error(audio):
    rec = Recorder(httpx.Response(400, json={"error": {"message": "decommissioned",
                                                       "code": "model_decommissioned"}}))
    with pytest.raises(ServiceError):
        make("groq", rec).transcribe(audio, "it")


def test_timeout_is_a_service_error(audio):
    with pytest.raises(ServiceError, match="timeout"):
        make("groq", Recorder(httpx.ReadTimeout)).transcribe(audio, "it")


def test_the_key_never_leaks(audio, caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(httpx.Response(401, json={"error": {"message": f"Invalid API Key: {KEY}"}}),
                   httpx.Response(401, text=f"bad key {KEY}"))
    provider = make("groq", rec)
    with pytest.raises(ServiceError) as exc:
        provider.transcribe(audio, "it")
    result = provider.check()
    for text in (str(exc.value), exc.value.detail, result.message, caplog.text):
        assert KEY not in text


def test_enrich_request_and_parsing():
    rec = Recorder(chat(json.dumps({"title": "Chiamare Marco", "summary": "Entro venerdì.",
                                    "tags": ["Lavoro", "#casa"]})))
    e = make("groq", rec).enrich("Devo chiamare Marco", "it")
    req = rec.requests[0]
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    payload = json.loads(req.content)
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["response_format"] == {"type": "json_object"}
    assert "italiano" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "Devo chiamare Marco"}
    assert (e.title, e.summary, e.tags, e.model) == ("Chiamare Marco", "Entro venerdì.",
                                                     ("lavoro", "casa"), "openai/gpt-oss-120b")


@pytest.mark.parametrize("response", [
    chat("non è JSON"), chat('{"title": 3}'), httpx.Response(200, json={"choices": []}),
    httpx.Response(400, json={"error": {"message": "bad request"}}),
    httpx.Response(200, text="<html>"),
])
def test_bad_enrichment_goes_to_the_next_provider(response):
    with pytest.raises(ServiceError):
        make("groq", Recorder(response)).enrich("testo", "it")


def test_enrich_without_text_model_does_not_call():
    rec = Recorder()
    with pytest.raises(ServiceError, match="modello di testo"):
        make("openai", rec, text_model="").enrich("testo", "it")
    assert rec.requests == []


def test_check():
    models = {"data": [{"id": "whisper-large-v3-turbo"}, {"id": "openai/gpt-oss-120b"}]}
    rec = Recorder(httpx.Response(200, json=models))
    result = make("groq", rec).check()
    assert result.ok and "whisper-large-v3-turbo" in result.message
    assert (rec.requests[0].method, str(rec.requests[0].url)) == (
        "GET", "https://api.groq.com/openai/v1/models")
    missing = make("groq", Recorder(httpx.Response(200, json={"data": [{"id": "altro"}]}))).check()
    assert not missing.ok and "openai/gpt-oss-120b" in missing.message
    assert not make("groq", Recorder(httpx.ConnectTimeout)).check().ok
