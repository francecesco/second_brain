import httpx
import pytest

from secondbrain.ai import base
from secondbrain.ai.base import ServiceError


def test_redact_removes_known_secrets_and_obviously_fake_key_shapes():
    # Chiavi finte e riconoscibili come tali (mai forma di chiave reale): niente scanner
    # di segreti innescati da questo file, spec `.superpowers/.../progress.md` Ruling P2.
    text = ("Invalid API Key gsk_test_abcdefgh0123456789 on "
            "https://x.test/v1?key=AIza-test-0123456789abcdefghij and sk-test-0123456789")
    out = base.redact(text)
    assert "gsk_test_abcdefgh" not in out
    assert "AIza-test" not in out
    assert "sk-test-0123456789" not in out
    assert base.redact("la chiave è segreto-noto", ["segreto-noto"]) == "la chiave è ***"


def test_redact_matches_realistic_length_fake_keys_too():
    # Stessa lunghezza indicativa di una chiave vera, ma con "test" dentro: non è la forma
    # esatta di una chiave reale, resta comunque presa dal pattern.
    text = ("gsk_test1234567890abcdefghijklmnopqrstuvwxyz1234567890ABCD "
            "sk-test1234567890abcdefghijklmnopqrstuvwxyz")
    out = base.redact(text)
    assert "test1234567890abcdefghijklmnopqrstuvwxyz" not in out


def test_redact_sk_pattern_has_a_word_boundary():
    out = base.redact("errore disk-full-error durante la scrittura")
    assert "disk-full-error" in out


def test_redact_collapses_whitespace_and_limits_length():
    out = base.redact("a\n\n   b " + "x" * 1000)
    assert out.startswith("a b ") and len(out) == base.MAX_ERROR_LEN


def test_error_message_and_code():
    limited = httpx.Response(429, json={"error": {"message": "Rate limit", "code": "rate_limit"}})
    assert (base.error_message(limited), base.error_code(limited)) == ("Rate limit", "rate_limit")
    assert base.error_message(httpx.Response(500, text="boom")) == "boom"
    assert base.error_message(httpx.Response(400, json={"error": "semplice"})) == "semplice"
    assert base.error_code(httpx.Response(400, json={"error": {"code": 400}})) is None


def raising(exc_type):
    def handler(request):
        raise exc_type("simulato", request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_turns_timeouts_and_network_errors_into_service_errors():
    with pytest.raises(ServiceError, match="groq: timeout"):
        base.send(raising(httpx.ReadTimeout), "GET", "https://x.test/", provider="groq", secrets=())
    with pytest.raises(ServiceError, match="errore di rete"):
        base.send(raising(httpx.ConnectError), "GET", "https://x.test/", provider="groq",
                  secrets=())


def test_parse_enrichment():
    e = base.parse_enrichment("groq", "m", '```json\n{"title": "T", "summary": "S", '
                              '"tags": ["#A"]}\n```', {"grezza": 1})
    assert (e.title, e.summary, e.tags, e.model, e.raw) == ("T", "S", ("a",), "m", {"grezza": 1})
    with pytest.raises(ServiceError, match="non JSON"):
        base.parse_enrichment("groq", "m", "ecco il titolo", {})
    with pytest.raises(ServiceError, match="fuori schema"):
        base.parse_enrichment("groq", "m", '{"title": "T"}', {})
    with pytest.raises(ServiceError, match="senza testo"):
        base.parse_enrichment("groq", "m", None, {})


def test_prompts_name_the_language_and_the_limits():
    prompt = base.enrich_prompt("it")
    assert "italiano" in prompt and "JSON" in prompt and "200" in prompt and "500" in prompt
    assert "inglese" in base.transcribe_prompt("en")


def test_check_models():
    ok = base.check_models({"a", "b"}, ["a", ""])
    assert ok.ok and "a" in ok.message
    missing = base.check_models({"a"}, ["a", "b"])
    assert not missing.ok and "b" in missing.message


def test_errors_carry_the_provider():
    assert str(ServiceError("groq", "HTTP 429: Rate limit")) == "groq: HTTP 429: Rate limit"
