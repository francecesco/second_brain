import errno
import json
import logging
import threading
from datetime import date, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from secondbrain import jobs, library
from secondbrain import settings_store as store
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.registry import build_provider
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.cli import main
from secondbrain.models import AiProvider, Capture, Job
from secondbrain.notefile import parse_note
from secondbrain.usage import usage_for_month
from secondbrain.worker import Shutdown, Worker, run_forever
from tests.ai_fakes import (DEFAULT_ENRICHMENT, DEFAULT_TEXT, FAKE_KEYS, FakeFactory,
                            FakeProvider, configure_providers)
from tests.helpers import DEV2, NOW, TEST_DB, TEST_SETTINGS_KEY, capture_by, make_wav, upload

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"


@pytest.fixture
def env(lan_client, db, settings, clock):
    box = store.SecretBox(settings.settings_key)
    configure_providers(db, box, NOW)
    upload(lan_client)
    groq, gemini = FakeProvider("groq"), FakeProvider("gemini")
    archive = Archive(settings.archive_dir)
    worker = Worker(sessionmaker=make_sessionmaker(db.get_bind()), archive=archive,
                    tz=settings.tz_archive, box=box, factory=FakeFactory(groq=groq, gemini=gemini),
                    clock=clock)
    return SimpleNamespace(worker=worker, groq=groq, gemini=gemini, box=box, archive=archive,
                           clock=clock, cap_id=capture_by(db, CID).id, client=lan_client)


def fresh(db, env) -> Capture:
    db.expire_all()
    return db.get(Capture, env.cap_id)


def job_of(db, env) -> Job | None:
    db.expire_all()
    return db.get(Job, env.cap_id)


def other_session(db):
    return make_sessionmaker(db.get_bind())()


def stages(provider, stage):
    return [call for call in provider.calls if call[0] == stage]


def test_processes_a_new_note(env, db):
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.language) == (
        DEFAULT_TEXT, "Chiamare Marco", "Chiamare Marco per il preventivo.", ["lavoro", "casa"], "it")
    assert (cap.ai_provider, cap.ai_transcribe_model, cap.ai_enrich_provider, cap.ai_enrich_model,
            cap.processed_at) == ("groq", "groq-stt", "groq", "groq-llm", NOW)
    note = parse_note(env.archive.read_text(cap.rel_path, ".md"))
    assert (note.title, note.transcript, note.tags) == ("Chiamare Marco", DEFAULT_TEXT,
                                                        ("lavoro", "casa"))
    raw = json.loads(env.archive.read_text(cap.rel_path, ".ai.json"))
    assert raw["transcribe"]["provider"] == "groq" and raw["transcribe"]["response"] == {
        "text": DEFAULT_TEXT}
    assert raw["enrich"]["model"] == "groq-llm"
    assert job_of(db, env).status == jobs.DONE
    usage = usage_for_month(db, date(2026, 9, 1))["groq"]
    assert (usage.audio_seconds, usage.calls) == (pytest.approx(1.0), 2)
    assert env.groq.calls == [("transcribe", "it"), ("enrich", DEFAULT_TEXT)]
    assert env.gemini.calls == []
    assert not env.worker.run_once()


def test_rate_limited_primary_falls_back_to_the_reserve(env, db):
    env.groq.transcripts = [ServiceError("groq", "HTTP 429: Rate limit")]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.ai_provider, cap.ai_enrich_provider) == ("gemini", "groq")  # ogni fase da capo
    usage = usage_for_month(db, date(2026, 9, 1))
    assert (usage["groq"].audio_seconds, usage["groq"].calls) == (0.0, 2)
    assert (usage["gemini"].audio_seconds, usage["gemini"].calls) == (pytest.approx(1.0), 1)


def test_rate_limit_storm_backs_off_then_fails(env, db):
    """Review Focus 5: tutti i provider in 429 → backoff, niente raffiche, poi failed."""
    env.groq.transcripts = [ServiceError("groq", "HTTP 429: quota esaurita")] * jobs.MAX_ATTEMPTS
    env.gemini.transcripts = [ServiceError("gemini", "HTTP 429: RESOURCE_EXHAUSTED")] * jobs.MAX_ATTEMPTS
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.next_run_at) == (
        jobs.QUEUED, 1, NOW + jobs.BACKOFF[0])
    assert not env.worker.run_once()  # prima del backoff non si ritenta
    assert len(env.groq.calls) == 1
    for _ in range(jobs.MAX_ATTEMPTS - 1):
        env.clock.now = job_of(db, env).next_run_at
        assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS)
    assert "groq: HTTP 429" in job.last_error and "gemini: HTTP 429" in job.last_error
    env.clock.now += timedelta(days=1)
    assert not env.worker.run_once()
    assert len(env.groq.calls) == jobs.MAX_ATTEMPTS


def test_content_error_fails_at_once(env, db):
    env.groq.transcripts = [ContentError("groq", "HTTP 400: audio non valido")]
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.last_error) == (
        jobs.FAILED, 1, "groq: HTTP 400: audio non valido")
    assert env.gemini.calls == []


def test_unreadable_audio_fails_at_once(env, db):
    env.archive.abs(fresh(db, env).rel_path).write_bytes(b"RIFFxxxxWAVEjunk")
    assert env.worker.run_once()
    job = job_of(db, env)
    assert job.status == jobs.FAILED and "illeggibile" in job.last_error
    assert env.groq.calls == []


def test_enrich_failure_does_not_transcribe_again(env, db):
    env.groq.enrichments = [ServiceError("groq", "HTTP 503")]
    env.gemini.enrichments = [ServiceError("gemini", "timeout")]
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.stage, job.attempts) == (jobs.QUEUED, jobs.STAGE_ENRICH, 1)
    assert fresh(db, env).transcript == DEFAULT_TEXT
    env.clock.now = job.next_run_at
    assert env.worker.run_once()
    assert job_of(db, env).status == jobs.DONE
    assert len(stages(env.groq, "transcribe")) == 1


def test_empty_transcript_is_done_without_enrichment(env, db):
    env.groq.transcripts = [""]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.transcript, cap.title_auto) == ("", None)
    assert parse_note(env.archive.read_text(cap.rel_path, ".md")).transcript == ""
    assert job_of(db, env).status == jobs.DONE
    assert stages(env.groq, "enrich") == []


def test_sleeps_when_paused_without_providers_or_without_key(env, db, settings, clock):
    store.set_paused(db, True)
    db.commit()
    assert not env.worker.run_once()
    store.set_paused(db, False)
    for row in db.scalars(select(AiProvider)):
        row.api_key_enc = None
    db.commit()
    assert not env.worker.run_once()
    no_key = Worker(sessionmaker=make_sessionmaker(db.get_bind()), archive=env.archive,
                    tz=ROME, box=store.SecretBox(None), factory=FakeFactory(), clock=clock)
    assert not no_key.run_once()
    assert job_of(db, env).status == jobs.QUEUED and env.groq.calls == []


def test_trash_during_transcription_discards_the_result(env, db):
    def trash_meanwhile():
        with other_session(db) as ui:
            library.trash_capture(ui, env.archive, env.cap_id, NOW)
        return DEFAULT_TEXT

    env.groq.transcripts = [trash_meanwhile]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert cap.trashed_at == NOW and cap.transcript is None
    assert not env.archive.derived_path(cap.rel_path, ".md").exists()
    assert job_of(db, env) is None
    assert stages(env.groq, "enrich") == []


def test_user_edit_during_enrichment_is_kept(env, db):
    """Review Focus 1: la correzione fatta mentre il modello risponde non si perde."""
    def user_edits_meanwhile():
        with other_session(db) as ui:
            library.edit_ai_field(ui, env.archive, ROME, env.cap_id, "summary",
                                  "Riassunto scritto da me")
        return ("Chiamare Marco", "Riassunto del modello", ("lavoro",))

    env.groq.enrichments = [user_edits_meanwhile]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.summary, cap.edited, cap.title_auto, cap.tags) == (
        "Riassunto scritto da me", ["summary"], "Chiamare Marco", ["lavoro"])
    note = parse_note(env.archive.read_text(cap.rel_path, ".md"))
    assert (note.summary, note.edited) == ("Riassunto scritto da me", ("summary",))


def test_reprocess_keeps_the_edited_fields(env, db):
    env.worker.run_once()
    library.edit_ai_field(db, env.archive, ROME, env.cap_id, "tags", "mio, tag")
    library.reprocess(db, env.cap_id, NOW)
    env.groq.enrichments = [("Nuovo titolo", "Nuovo riassunto", ("altro",))]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.title_auto, cap.summary, cap.tags, cap.edited) == (
        "Nuovo titolo", "Nuovo riassunto", ["mio", "tag"], ["tags"])
    assert len(stages(env.groq, "transcribe")) == 2


def test_reprocess_after_editing_the_transcript_skips_transcription(env, db):
    env.worker.run_once()
    library.edit_ai_field(db, env.archive, ROME, env.cap_id, "transcript", "Testo corretto a mano")
    assert library.reprocess(db, env.cap_id, NOW).stage == jobs.STAGE_ENRICH
    assert env.worker.run_once()
    assert env.groq.calls[-1] == ("enrich", "Testo corretto a mano")
    assert len(stages(env.groq, "transcribe")) == 1
    assert fresh(db, env).transcript == "Testo corretto a mano"


def test_settings_are_read_again_for_every_job(env, db):
    env.worker.run_once()
    store.move_provider(db, "gemini", store.MOVE_UP, NOW)
    store.set_language(db, "en")
    db.commit()
    upload(env.client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260923_080000",
           ts="2026-09-23T08:00:00Z", device=DEV2)
    assert env.worker.run_once()
    second = capture_by(db, "cap_20260923_080000")
    assert (second.ai_provider, second.language) == ("gemini", "en")


def test_shutdown_puts_the_job_back(env, db):
    env.groq.transcripts = [Shutdown()]
    with pytest.raises(Shutdown):
        env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.locked_until) == (jobs.QUEUED, 0, None)


def test_disk_error_goes_back_to_the_queue(env, db, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "disco pieno")

    monkeypatch.setattr(Archive, "write_text", full)
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.last_error) == (
        jobs.QUEUED, 1, "errore locale: OSError")
    assert fresh(db, env).transcript is None


def test_run_forever_stops():
    stop = threading.Event()

    class Once:
        calls = 0

        def run_once(self):
            self.calls += 1
            stop.set()
            return False

    once = Once()
    run_forever(once, stop, interval=0)
    assert once.calls == 1

    class Interrupted:
        def run_once(self):
            raise Shutdown

    run_forever(Interrupted(), threading.Event(), interval=0)  # esce senza sollevare


def test_cli_worker_wiring(monkeypatch, settings):
    seen = {}
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    monkeypatch.setenv("SETTINGS_KEY", TEST_SETTINGS_KEY)
    monkeypatch.setattr("secondbrain.cli.install_signal_handlers", lambda stop: None)
    monkeypatch.setattr("secondbrain.cli.run_forever",
                        lambda worker, stop: seen.update(worker=worker))
    assert main(["worker"]) == 0
    assert isinstance(seen["worker"], Worker) and seen["worker"].box.available


def lease_taken_meanwhile(env, result):
    """Il lease scade durante la chiamata e la nota viene rimessa in coda da "Rielabora"."""
    def action():
        env.clock.now = NOW + jobs.LEASE + timedelta(minutes=1)
        with env.worker.sessionmaker() as ui:
            library.reprocess(ui, env.cap_id, env.clock.now)
        return result
    return action


def warnings_in(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_result_is_discarded_when_the_lease_was_lost(env, db, caplog):
    env.groq.transcripts = [lease_taken_meanwhile(env, DEFAULT_TEXT)]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert cap.transcript is None
    assert not env.archive.derived_path(cap.rel_path, ".md").exists()
    assert not env.archive.derived_path(cap.rel_path, ".ai.json").exists()
    job = job_of(db, env)
    assert (job.status, job.stage, job.attempts, job.last_error) == (
        jobs.QUEUED, jobs.STAGE_TRANSCRIBE, 0, None)
    assert stages(env.groq, "enrich") == []
    assert any("scartato" in message for message in warnings_in(caplog))


def test_failure_after_losing_the_lease_leaves_the_job_alone(env, db):
    env.groq.transcripts = [lease_taken_meanwhile(env, ServiceError("groq", "HTTP 503"))]
    env.gemini.transcripts = [ServiceError("gemini", "HTTP 503")]
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.last_error) == (jobs.QUEUED, 0, None)


def test_expired_running_job_of_a_trashed_note_is_dropped(env, db):
    jobs.claim(db, NOW)  # un worker di prima l'ha presa e si è fermato a metà
    library.trash_capture(db, env.archive, env.cap_id, NOW)
    assert job_of(db, env).status == jobs.RUNNING  # il cestino non tocca il lavoro in corso
    env.clock.now = NOW + jobs.LEASE + timedelta(minutes=1)
    assert env.worker.run_once()
    assert job_of(db, env) is None
    assert env.groq.calls == [] and env.gemini.calls == []
    assert fresh(db, env).transcript is None


def test_unreadable_ai_json_is_replaced_with_a_warning(env, db, caplog):
    env.groq.enrichments = [ServiceError("groq", "HTTP 503")]
    env.gemini.enrichments = [ServiceError("gemini", "HTTP 503")]
    assert env.worker.run_once()
    cap = fresh(db, env)
    env.archive.write_text(cap.rel_path, ".ai.json", "{non è json")
    env.clock.now = job_of(db, env).next_run_at
    assert env.worker.run_once()
    raw = json.loads(env.archive.read_text(cap.rel_path, ".ai.json"))
    assert raw["schema_version"] == 1 and raw["enrich"]["provider"] == "groq"
    assert "transcribe" not in raw
    assert job_of(db, env).status == jobs.DONE
    assert any(".ai.json" in message for message in warnings_in(caplog))


def test_usage_failure_does_not_fail_the_note(env, db, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OperationalError("INSERT INTO ai_usage", {}, Exception("database giù"))

    monkeypatch.setattr("secondbrain.worker.record_usage", broken)
    assert env.worker.run_once()
    assert job_of(db, env).status == jobs.DONE
    assert fresh(db, env).transcript == DEFAULT_TEXT
    assert any("utilizzo" in message for message in warnings_in(caplog))
    assert all(r.exc_info for r in caplog.records if "utilizzo" in r.getMessage())


def test_shutdown_requeue_failure_is_logged(env, db, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OperationalError("UPDATE jobs", {}, Exception("database giù"))

    env.groq.transcripts = [Shutdown()]
    monkeypatch.setattr(jobs, "release", broken)
    with pytest.raises(Shutdown):
        env.worker.run_once()
    assert job_of(db, env).status == jobs.RUNNING  # lo riprende il lease
    assert any("coda" in message for message in warnings_in(caplog))
    assert all(r.exc_info for r in caplog.records if "rimettere in coda" in r.getMessage())


def test_api_keys_never_leak_through_the_worker(env, db, caplog):
    """Review Focus 3 con gli adattatori veri: la chiave ripetuta dal provider non esce mai."""
    caplog.set_level(logging.DEBUG)
    groq_key, gemini_key = FAKE_KEYS["groq"], FAKE_KEYS["gemini"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.groq.com":
            message = f"Rate limit for {groq_key}, see https://console.groq.com/?key={groq_key}"
            return httpx.Response(429, json={"error": {"message": message}})
        raise httpx.ConnectError(f"impossibile raggiungere {request.url}?key={gemini_key}",
                                 request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raised: list[str] = []

    class Spy:
        def __init__(self, inner):
            self.inner, self.name = inner, inner.name

        def transcribe(self, audio, language):
            try:
                return self.inner.transcribe(audio, language)
            except Exception as exc:
                raised.append(f"{exc!s} {exc!r}")
                raise

    env.worker.factory = lambda config: Spy(build_provider(config, client))
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts) == (jobs.QUEUED, 1)
    assert "groq: HTTP 429" in job.last_error and "gemini: errore di rete" in job.last_error
    assert len(raised) == 2
    for key in (groq_key, gemini_key):
        assert key not in job.last_error
        assert all(key not in text for text in raised)
        assert key not in caplog.text
    client.close()


def test_transcript_wiped_before_enrichment_goes_back_to_transcription(env, db):
    """Final review 1: se tra le due fasi il catalogo perde la trascrizione (un rescan che
    ha letto il `.md` prima che esistesse), il worker torna a trascrivere invece di
    riscrivere il `.md` vuoto."""
    def catalog_wiped_meanwhile():
        with other_session(db) as other:
            other.execute(update(Capture).where(Capture.id == env.cap_id)
                          .values(transcript=None))
            other.commit()
        return DEFAULT_ENRICHMENT

    env.groq.enrichments = [catalog_wiped_meanwhile]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert parse_note(env.archive.read_text(cap.rel_path, ".md")).transcript == DEFAULT_TEXT
    job = job_of(db, env)
    assert (job.status, job.stage, job.attempts) == (jobs.QUEUED, jobs.STAGE_TRANSCRIBE, 0)
    assert cap.title_auto is None


def test_audio_moved_away_mid_job_is_retried(env, db):
    """Final review 3: il WAV spostato (correzione della data) tra `_begin` e la lettura
    non è "audio illeggibile" per sempre: si riprova con il backoff."""
    path = env.archive.abs(fresh(db, env).rel_path)
    data = path.read_bytes()
    path.unlink()
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.next_run_at) == (jobs.QUEUED, 1, NOW + jobs.BACKOFF[0])
    assert "illeggibile" not in job.last_error
    path.write_bytes(data)
    env.clock.now = job.next_run_at
    assert env.worker.run_once()
    assert job_of(db, env).status == jobs.DONE


def test_empty_transcript_on_reprocess_clears_the_old_enrichment(env, db):
    """Final review 4: "Rielabora" senza parlato non lascia titolo, riassunto e tag vecchi;
    i campi corretti a mano restano."""
    env.worker.run_once()
    library.edit_ai_field(db, env.archive, ROME, env.cap_id, "tags", "mio")
    library.reprocess(db, env.cap_id, NOW)
    env.groq.transcripts = [""]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.ai_enrich_provider) == (
        "", None, None, ["mio"], None)
    note = parse_note(env.archive.read_text(cap.rel_path, ".md"))
    assert (note.title, note.summary, note.tags) == (None, None, ("mio",))
    assert job_of(db, env).status == jobs.DONE


def test_bad_settings_key_keeps_the_worker_idle(env, db, clock, caplog):
    """Final review 9: SETTINGS_KEY non valida = worker fermo, con il motivo nel log."""
    caplog.set_level(logging.INFO)
    worker = Worker(sessionmaker=make_sessionmaker(db.get_bind()), archive=env.archive,
                    tz=ROME, box=store.SecretBox(None, invalid=True), factory=FakeFactory(),
                    clock=clock)
    assert not worker.run_once()
    assert "SETTINGS_KEY non valida" in caplog.text
    assert job_of(db, env).status == jobs.QUEUED


def test_cli_worker_with_a_bad_settings_key_idles(monkeypatch, settings, caplog):
    seen = {}
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    monkeypatch.setenv("SETTINGS_KEY", "corta-e-sbagliata")
    monkeypatch.setattr("secondbrain.cli.install_signal_handlers", lambda stop: None)
    monkeypatch.setattr("secondbrain.cli.run_forever",
                        lambda worker, stop: seen.update(worker=worker))
    assert main(["worker"]) == 0
    assert not seen["worker"].box.available and seen["worker"].box.invalid
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("SETTINGS_KEY non valida" in m for m in errors)
    assert "corta-e-sbagliata" not in caplog.text
