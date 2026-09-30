import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select

from secondbrain import jobs, library
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.cli import main
from secondbrain.models import Capture, Job
from tests.helpers import DEV2, NOW, TEST_DB, capture_by, make_wav, upload

CID = "cap_20260923_191530"
OLD = ("cap_20260801_080000", "cap_20260802_080000")


def job_of(db, capture_id) -> Job | None:
    db.expire_all()
    return db.get(Job, capture_id)


def jobs_count(db) -> int:
    return db.scalar(select(func.count()).select_from(Job))


@pytest.fixture
def note(lan_client, db):
    upload(lan_client)
    return capture_by(db, CID).id


def test_ingest_queues_the_new_note(note, db):
    job = job_of(db, note)
    assert (job.status, job.stage, job.priority, job.attempts, job.next_run_at) == (
        jobs.QUEUED, jobs.STAGE_TRANSCRIBE, jobs.PRIORITY_NORMAL, 0, NOW)


def test_device_retry_of_a_processed_note_changes_nothing(note, lan_client, db):
    jobs.mark_done(db, note, NOW)
    db.commit()
    assert upload(lan_client).status_code == 409
    assert job_of(db, note).status == jobs.DONE
    assert jobs_count(db) == 1


def test_new_notes_pass_the_backlog(lan_client, db):
    """Review Focus 4: l'arretrato in coda da un'ora non fa aspettare una nota nuova."""
    for i, cid in enumerate(OLD):
        upload(lan_client, make_wav(fill=bytes([i + 2, 0])), capture_id=cid,
               ts=f"2026-08-0{i + 1}T08:00:00Z")
    old = {capture_by(db, cid).id for cid in OLD}
    for capture_id in old:
        jobs.enqueue(db, capture_id, NOW - timedelta(hours=1), priority=jobs.PRIORITY_LOW)
    db.commit()
    upload(lan_client)
    new = capture_by(db, CID).id
    assert jobs.claim(db, NOW).capture_id == new
    assert {jobs.claim(db, NOW).capture_id, jobs.claim(db, NOW).capture_id} == old
    assert jobs.claim(db, NOW) is None


def test_claim_takes_a_lease(note, db):
    job = jobs.claim(db, NOW)
    assert (job.status, job.locked_until) == (jobs.RUNNING, NOW + jobs.LEASE)
    assert jobs.claim(db, NOW + timedelta(minutes=1)) is None


def test_expired_lease_is_taken_again_and_counted(note, db):
    jobs.claim(db, NOW)
    later = NOW + jobs.LEASE + timedelta(seconds=1)
    job = jobs.claim(db, later)
    assert (job.capture_id, job.status, job.attempts, job.last_error) == (
        note, jobs.RUNNING, 1, jobs.INTERRUPTED)
    assert job.locked_until == later + jobs.LEASE


def test_held_recognises_only_the_current_lease(note, db):
    lease = jobs.claim(db, NOW).locked_until
    assert jobs.held(db, note, lease, NOW) is not None
    db.rollback()
    assert jobs.held(db, note, lease, lease) is None  # scaduto
    db.rollback()
    renewed = jobs.advance(db, note, jobs.STAGE_ENRICH, NOW + timedelta(minutes=1))
    db.commit()
    assert jobs.held(db, note, lease, NOW) is None  # lease rinnovato: vale solo il nuovo
    assert jobs.held(db, note, renewed.locked_until, NOW) is not None
    db.rollback()
    jobs.enqueue(db, note, NOW)
    db.commit()
    assert jobs.held(db, note, renewed.locked_until, NOW) is None  # rimesso in coda
    db.rollback()


def test_a_job_that_keeps_crashing_the_worker_ends_failed(note, db):
    now = NOW
    for _ in range(jobs.MAX_ATTEMPTS):
        jobs.claim(db, now)
        now += jobs.LEASE + timedelta(seconds=1)
    assert jobs.claim(db, now) is None
    job = job_of(db, note)
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS)


def test_two_workers_never_get_the_same_job(lan_client, db, engine):
    upload(lan_client)
    upload(lan_client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260923_080000",
           ts="2026-09-23T08:00:00Z", device=DEV2)
    first, second = capture_by(db, CID).id, capture_by(db, "cap_20260923_080000").id
    other = make_sessionmaker(engine)()
    try:
        other.get(Job, first, with_for_update=True)  # l'altro worker sta prendendo questo
        assert jobs.claim(db, NOW).capture_id == second
    finally:
        other.rollback()
        other.close()


def test_backoff_then_failed():
    job = Job(attempts=0, status=jobs.RUNNING)
    for i, delay in enumerate(jobs.BACKOFF, start=1):
        jobs.record_failure(job, NOW, "groq: HTTP 429")
        assert (job.status, job.attempts, job.next_run_at) == (jobs.QUEUED, i, NOW + delay)
    jobs.record_failure(job, NOW, "groq: HTTP 429")
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS)


def test_permanent_failure_and_long_errors():
    job = Job(attempts=0, status=jobs.RUNNING)
    jobs.record_failure(job, NOW, "x" * 5000, permanent=True)
    assert (job.status, job.attempts, len(job.last_error)) == (
        jobs.FAILED, 1, jobs.MAX_LAST_ERROR_LEN)


def test_trash_cancels_the_job_and_restore_does_not_queue(note, db, settings):
    archive = Archive(settings.archive_dir)
    library.trash_capture(db, archive, note, NOW)
    assert job_of(db, note) is None
    library.restore_capture(db, archive, note)
    assert job_of(db, note) is None


def test_trash_leaves_a_running_job_to_the_worker(note, db, settings):
    jobs.claim(db, NOW)
    library.trash_capture(db, Archive(settings.archive_dir), note, NOW)
    assert job_of(db, note).status == jobs.RUNNING


def test_reprocess(note, db):
    jobs.mark_done(db, note, NOW)
    db.commit()
    job = library.reprocess(db, note, NOW)
    assert (job.status, job.stage, job.attempts) == (jobs.QUEUED, jobs.STAGE_TRANSCRIBE, 0)
    cap = db.get(Capture, note)
    cap.transcript, cap.edited = "corretto a mano", ["transcript"]
    db.commit()
    assert library.reprocess(db, note, NOW).stage == jobs.STAGE_ENRICH


def test_reprocess_refuses_a_running_job_but_not_a_dead_one(note, db):
    jobs.claim(db, NOW)
    with pytest.raises(jobs.JobRunning):
        library.reprocess(db, note, NOW + timedelta(minutes=1))
    db.rollback()
    assert library.reprocess(db, note, NOW + jobs.LEASE * 2).status == jobs.QUEUED


def test_reprocess_refused_in_trash(note, db, settings):
    library.trash_capture(db, Archive(settings.archive_dir), note, NOW)
    with pytest.raises(library.InTrash):
        library.reprocess(db, note, NOW)


def test_backfill_takes_notes_without_transcript(recordings, db, settings):
    db.execute(delete(Job))  # come le note archiviate prima dell'elaborazione AI
    db.commit()
    with_text = capture_by(db, CID)
    with_text.transcript = "già trascritta"
    db.commit()
    library.trash_capture(db, Archive(settings.archive_dir),
                          capture_by(db, "cap_20260923_080000").id, NOW)
    assert jobs.count_backfill(db) == 1
    assert jobs.enqueue_backfill(db, NOW) == 1
    db.commit()
    [job] = db.scalars(select(Job)).all()
    assert (job.capture_id, job.priority) == (capture_by(db, "cap_20260810_070000").id,
                                              jobs.PRIORITY_LOW)
    assert jobs.count_backfill(db) == 0


def test_deleting_a_capture_deletes_its_job(note, db, settings):
    archive = Archive(settings.archive_dir)
    library.trash_capture(db, archive, note, NOW)
    jobs.enqueue(db, note, NOW)
    db.commit()
    library.delete_capture(db, archive, note)
    assert jobs_count(db) == 0


def test_queue_counts(recordings, db):
    jobs.claim(db, NOW)
    assert jobs.queue_counts(db) == {"queued": 2, "running": 1, "done": 0, "failed": 0}


@pytest.fixture
def cli_env(monkeypatch, settings):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))


def test_cli_process(note, db, cli_env, capsys):
    jobs.mark_done(db, note, NOW)
    db.commit()
    assert main(["process", str(note)]) == 0
    assert "in coda (fase transcribe)" in capsys.readouterr().out
    assert job_of(db, note).status == jobs.QUEUED


def test_cli_process_backfill(note, db, cli_env, capsys):
    db.execute(delete(Job))
    db.commit()
    assert main(["process", "--backfill"]) == 0
    assert "Messe in coda 1 note" in capsys.readouterr().out
    assert job_of(db, note).priority == jobs.PRIORITY_LOW


@pytest.mark.parametrize("argv,message", [
    (["process"], "oppure --backfill"),
    (["process", "non-un-uuid"], "id non valido"),
    (["process", str(uuid.UUID(int=1))], "sconosciuta"),
])
def test_cli_process_errors(db, cli_env, capsys, argv, message):
    assert main(argv) == 1
    assert message in capsys.readouterr().err
