"""Coda di elaborazione in Postgres: un lavoro per nota (spec §7).

Stati `queued` → `running` → `done` | `failed`; fasi `transcribe` → `enrich`. Il worker
prende il lavoro con `FOR UPDATE SKIP LOCKED` e un lease: se muore a metà, allo scadere
del lease il lavoro si riprende (contando il tentativo).
"""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from .models import Capture, Job

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
STATUSES = (QUEUED, RUNNING, DONE, FAILED)
ACTIVE = (QUEUED, RUNNING)
STAGE_TRANSCRIBE = "transcribe"
STAGE_ENRICH = "enrich"
PRIORITY_NORMAL = 100  # note appena arrivate e "Rielabora"
PRIORITY_LOW = 10      # arretrato e rescan: passano dopo le note nuove
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30),
           timedelta(hours=2), timedelta(hours=6))
MAX_ATTEMPTS = len(BACKOFF) + 1  # al sesto tentativo fallito la nota è failed
# Il lease deve superare il caso peggiore di un lavoro: tre provider in timeout sulla
# trascrizione (3 × 300 s) e sull'arricchimento (3 × 120 s) fanno 21 minuti.
LEASE = timedelta(minutes=30)
MAX_LAST_ERROR_LEN = 1000  # gli errori di più provider in fila, ognuno già limitato
INTERRUPTED = "elaborazione interrotta: il worker si è fermato a metà"


class JobRunning(Exception):
    """La nota è in elaborazione proprio ora: non si rimette in coda."""


def get_job(s: Session, capture_id: uuid.UUID) -> Job | None:
    return s.get(Job, capture_id, populate_existing=True)


def _locked(s: Session, capture_id: uuid.UUID) -> Job | None:
    return s.get(Job, capture_id, with_for_update=True, populate_existing=True)


def enqueue(s: Session, capture_id: uuid.UUID, now: datetime, *,
            priority: int = PRIORITY_NORMAL, stage: str = STAGE_TRANSCRIBE) -> Job:
    """Crea il lavoro o lo rimette da capo in coda (tentativi azzerati). Niente commit."""
    job = _locked(s, capture_id)
    if job is None:
        job = Job(capture_id=capture_id)
        s.add(job)
    job.status = QUEUED
    job.stage = stage
    job.priority = priority
    job.attempts = 0
    job.next_run_at = now
    job.locked_until = None
    job.last_error = None
    job.updated_at = now
    s.flush()
    return job


def is_active(s: Session, capture_id: uuid.UUID) -> bool:
    job = get_job(s, capture_id)
    return job is not None and job.status in ACTIVE


def requeue(s: Session, capture: Capture, now: datetime) -> Job:
    """"Rielabora": se la trascrizione è stata corretta a mano si riparte dall'arricchimento."""
    job = _locked(s, capture.id)
    if (job is not None and job.status == RUNNING and job.locked_until is not None
            and job.locked_until > now):
        raise JobRunning(str(capture.id))
    edited_transcript = "transcript" in (capture.edited or []) and capture.transcript is not None
    return enqueue(s, capture.id, now,
                   stage=STAGE_ENRICH if edited_transcript else STAGE_TRANSCRIBE)


def _backfill():
    """Note fuori dal cestino senza trascrizione (quindi senza `.md`) e non già in coda."""
    return (select(Capture.id).outerjoin(Job, Job.capture_id == Capture.id)
            .where(Capture.trashed_at.is_(None), Capture.transcript.is_(None),
                   or_(Job.status.is_(None), Job.status.not_in(ACTIVE))))


def count_backfill(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(_backfill().subquery()))


def enqueue_backfill(s: Session, now: datetime) -> int:
    ids = list(s.scalars(_backfill().order_by(Capture.recorded_at.desc())))
    for capture_id in ids:
        enqueue(s, capture_id, now, priority=PRIORITY_LOW)
    return len(ids)


def record_failure(job: Job, now: datetime, error: str, *, permanent: bool = False) -> None:
    job.attempts += 1
    job.last_error = error[:MAX_LAST_ERROR_LEN]
    job.locked_until = None
    job.updated_at = now
    if permanent or job.attempts >= MAX_ATTEMPTS:
        job.status = FAILED
    else:
        job.status = QUEUED
        job.next_run_at = now + BACKOFF[job.attempts - 1]


def claim(s: Session, now: datetime) -> Job | None:
    """Il lavoro con priorità più alta e attesa più lunga tra quelli pronti; fa commit."""
    due = or_(and_(Job.status == QUEUED, Job.next_run_at <= now),
              and_(Job.status == RUNNING, Job.locked_until < now))
    stmt = (select(Job).where(due).order_by(Job.priority.desc(), Job.next_run_at).limit(1)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
    while True:
        job = s.scalars(stmt).first()
        if job is None:
            s.commit()
            return None
        if job.status == RUNNING:  # lease scaduto: il worker di prima si è fermato a metà
            record_failure(job, now, INTERRUPTED)
            if job.status == FAILED:
                s.commit()
                continue
        job.status = RUNNING
        job.locked_until = now + LEASE
        job.updated_at = now
        s.commit()
        return job


def held(s: Session, capture_id: uuid.UUID, locked_until: datetime,
         now: datetime) -> Job | None:
    """Il lavoro, bloccato, se è ancora del worker che l'ha preso con quel lease.

    Il lease non ha un proprietario: lo riconosce `locked_until`, che cambia a ogni claim.
    None se il lavoro è sparito, non è più in corso, è stato ripreso o il lease è scaduto.
    """
    job = _locked(s, capture_id)
    if (job is None or job.status != RUNNING or job.locked_until != locked_until
            or locked_until <= now):
        return None
    return job


def advance(s: Session, capture_id: uuid.UUID, stage: str, now: datetime) -> Job | None:
    """Fase successiva nello stesso giro del worker: lease rinnovato, tentativi azzerati."""
    job = _locked(s, capture_id)
    if job is not None:
        job.stage = stage
        job.attempts = 0
        job.last_error = None
        job.locked_until = now + LEASE
        job.updated_at = now
    return job


def mark_done(s: Session, capture_id: uuid.UUID, now: datetime) -> None:
    job = _locked(s, capture_id)
    if job is not None:
        job.status = DONE
        job.attempts = 0
        job.last_error = None
        job.locked_until = None
        job.updated_at = now


def release(s: Session, capture_id: uuid.UUID, now: datetime, stage: str | None = None) -> None:
    """Di nuovo in coda subito, senza contare un tentativo (arresto del worker)."""
    job = _locked(s, capture_id)
    if job is not None:
        job.status = QUEUED
        job.next_run_at = now
        job.locked_until = None
        job.updated_at = now
        if stage is not None:
            job.stage = stage


def fail(s: Session, capture_id: uuid.UUID, now: datetime, error: str, *,
         permanent: bool) -> Job | None:
    job = _locked(s, capture_id)
    if job is not None:
        record_failure(job, now, error, permanent=permanent)
    return job


def drop(s: Session, capture_id: uuid.UUID) -> None:
    s.execute(delete(Job).where(Job.capture_id == capture_id))


def cancel_for_trash(s: Session, capture_id: uuid.UUID) -> None:
    """Nel cestino: via il lavoro, salvo quello in corso (lo scarta il worker)."""
    s.execute(delete(Job).where(Job.capture_id == capture_id, Job.status != RUNNING))


def queue_counts(s: Session) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for status, n in s.execute(select(Job.status, func.count()).group_by(Job.status)):
        counts[status] = n
    return counts
