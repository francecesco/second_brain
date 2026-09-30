from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain.ai import chain
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.registry import ProviderConfig
from secondbrain.usage import month_start, record_usage, usage_for_month
from tests.ai_fakes import FakeFactory, FakeProvider

GROQ = ProviderConfig("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b", "gsk_test_1")
GEMINI = ProviderConfig("gemini", "gemini-3.8-flash", "gemini-3.8-flash", "AIza-test-2")
OPENAI = ProviderConfig("openai", "gpt-4o-mini-transcribe", "", "sk-test-3")


class StubAudio:
    duration_s = 12.5


@pytest.fixture
def calls():
    return []


def on_call(calls):
    return lambda name, seconds: calls.append((name, seconds))


def test_primary_answers_and_the_reserve_is_not_called(calls):
    groq, gemini = FakeProvider("groq"), FakeProvider("gemini")
    config, t = chain.transcribe([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini),
                                 StubAudio(), "it", on_call(calls))
    assert (config.name, t.model) == ("groq", "groq-stt")
    assert gemini.calls == [] and calls == [("groq", 12.5)]


def test_rate_limited_primary_falls_back_to_the_reserve(calls):
    groq = FakeProvider("groq", transcripts=[ServiceError("groq", "HTTP 429: Rate limit")])
    gemini = FakeProvider("gemini")
    config, _ = chain.transcribe([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini),
                                 StubAudio(), "it", on_call(calls))
    assert config.name == "gemini" and calls == [("groq", 0.0), ("gemini", 12.5)]


def test_content_error_stops_the_chain(calls):
    groq = FakeProvider("groq", transcripts=[ContentError("groq", "HTTP 400: audio non valido")])
    gemini = FakeProvider("gemini")
    with pytest.raises(ContentError):
        chain.transcribe([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini), StubAudio(), "it",
                         on_call(calls))
    assert gemini.calls == [] and calls == [("groq", 0.0)]


def test_all_failing_reports_every_error(calls):
    groq = FakeProvider("groq", enrichments=[ServiceError("groq", "HTTP 503: giù")])
    gemini = FakeProvider("gemini", enrichments=[ServiceError("gemini", "timeout")])
    with pytest.raises(chain.AllProvidersFailed) as exc:
        chain.enrich([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini), "testo", "it",
                     on_call(calls))
    assert str(exc.value) == "groq: HTTP 503: giù; gemini: timeout"
    assert len(exc.value.errors) == 2


def test_provider_without_a_model_for_the_stage_is_skipped(calls):
    openai, gemini = FakeProvider("openai"), FakeProvider("gemini")
    config, _ = chain.enrich([OPENAI, GEMINI], FakeFactory(openai=openai, gemini=gemini), "testo",
                             "it", on_call(calls))
    assert config.name == "gemini" and openai.calls == []
    config, _ = chain.transcribe([OPENAI, GEMINI], FakeFactory(openai=openai, gemini=gemini),
                                 StubAudio(), "it", on_call(calls))
    assert config.name == "openai"


def test_no_usable_provider(calls):
    with pytest.raises(chain.AllProvidersFailed, match="nessun provider"):
        chain.enrich([], FakeFactory(), "testo", "it", on_call(calls))


def test_usage_accumulates_per_provider_and_month(db):
    september, october = date(2026, 9, 1), date(2026, 10, 1)
    record_usage(db, "groq", september, 60.0)
    record_usage(db, "groq", september, 30.0)
    record_usage(db, "groq", october, 5.0)
    record_usage(db, "gemini", september, 0.0)
    db.commit()
    usage = usage_for_month(db, september)
    assert (usage["groq"].audio_seconds, usage["groq"].calls) == (90.0, 2)
    assert (usage["gemini"].audio_seconds, usage["gemini"].calls) == (0.0, 1)
    assert usage_for_month(db, october)["groq"].calls == 1


def test_month_is_the_local_one():
    rome = ZoneInfo("Europe/Rome")
    assert month_start(datetime.fromisoformat("2026-09-30T22:30:00+00:00"), rome) == date(2026, 10, 1)
