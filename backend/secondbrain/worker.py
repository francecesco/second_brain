"""Worker dell'elaborazione AI (spec §4, §7): un lavoro alla volta dalla coda in Postgres.

Fase `transcribe`: FLAC temporaneo, catena dei provider, `.md` con il solo corpo e
`.ai.json`, poi `enrich`. Fase `enrich`: testo dal catalogo, catena, titolo, riassunto e
tag nel `.md` e nel catalogo, `done`. Le chiamate ai provider avvengono senza lock; il
risultato si scrive sotto il lock della riga, rileggendo percorso, cestino ed `edited`.

Prima di scrivere si blocca la riga della nota e poi quella del lavoro (lo stesso ordine
di `library.trash_capture` e `library.reprocess`) e si controlla di avere ancora il lease:
se nel frattempo è scaduto o la nota è stata rimessa in coda, il risultato si scarta.
"""
import json
import logging
import signal
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from . import jobs, notefile, notes
from . import settings_store as store
from .ai import chain
from .ai.audio import AudioError, AudioSource
from .ai.base import ContentError
from .ai.registry import ProviderConfig
from .archive import AI_SUFFIX, Archive
from .clock import utcnow
from .models import Capture, Job
from .usage import month_start, record_usage

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
POLL_INTERVAL_S = 5.0
AI_SCHEMA_VERSION = 1
TEMP_PREFIX = "secondbrain-audio-"
IDLE_NO_KEY = "SETTINGS_KEY non impostata"
IDLE_PAUSED = "elaborazione in pausa"
IDLE_NO_PROVIDER = "nessun provider abilitato con una chiave"


class Shutdown(BaseException):
    """Arresto richiesto (SIGTERM/SIGINT): il lavoro in corso torna in coda.

    BaseException, così nessun `except Exception` lo inghiotte a metà strada.
    """


@dataclass
class _Lease:
    """Il lavoro preso da questo worker: lo riconosce `until`, rinnovato a ogni fase."""
    capture_id: uuid.UUID
    until: datetime


def _ai_record(provider: str, model: str, at: datetime, raw: dict[str, Any]) -> dict:
    return {"provider": provider, "model": model, "at": at.isoformat(), "response": raw}


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


@dataclass
class Worker:
    sessionmaker: sessionmaker[Session]
    archive: Archive
    tz: ZoneInfo
    box: store.SecretBox
    factory: chain.ProviderFactory
    clock: Callable[[], datetime] = utcnow
    _idle_reason: str | None = field(default=None, init=False)

    def run_once(self) -> bool:
        """Elabora al più un lavoro; False se non c'era niente da fare o non si può."""
        with self.sessionmaker() as s:
            reason, configs = self._check(s)
            if reason is not None:
                self._idle(reason)
                return False
            language = store.get_language(s)  # impostazioni rilette a ogni lavoro
            job = jobs.claim(s, self.clock())
            if job is None:
                return False
            capture_id, lease_until = job.capture_id, job.locked_until
        self._idle(None)
        self.process(capture_id, configs, language, lease_until=lease_until)
        return True

    def _check(self, s: Session) -> tuple[str | None, list[ProviderConfig]]:
        if not self.box.available:
            return IDLE_NO_KEY, []
        if store.is_paused(s):
            return IDLE_PAUSED, []
        configs = store.provider_configs(s, self.box)
        return (None, configs) if configs else (IDLE_NO_PROVIDER, [])

    def _idle(self, reason: str | None) -> None:
        if reason is not None and reason != self._idle_reason:
            log.info("worker fermo: %s", reason)
        self._idle_reason = reason

    def process(self, capture_id: uuid.UUID, configs: list[ProviderConfig], language: str, *,
                lease_until: datetime) -> None:
        """Il lavoro preso con `jobs.claim`; `lease_until` è il suo `locked_until`."""
        lease = _Lease(capture_id, lease_until)
        try:
            start = self._begin(lease)
            if start is None:
                return
            stage, rel_path, text = start
            if stage == jobs.STAGE_TRANSCRIBE:
                text = self._transcribe(lease, rel_path, configs, language)
                if text is None:
                    return
            self._enrich(lease, text, configs, language)
        except Shutdown:
            self._release(lease)
            raise
        except (ContentError, AudioError) as exc:
            self._fail(lease, str(exc), permanent=True)
        except chain.AllProvidersFailed as exc:
            self._fail(lease, str(exc), permanent=False)
        except Exception as exc:  # noqa: BLE001 - disco pieno, DB, bug: si riprova con backoff
            log.exception("elaborazione di %s interrotta da un errore locale", capture_id)
            self._fail(lease, f"errore locale: {type(exc).__name__}", permanent=False)

    def _hold(self, s: Session, lease: _Lease) -> tuple[Capture, Job] | None:
        """Nota e lavoro bloccati, in quest'ordine, se il risultato si può ancora scrivere.

        None (e niente da scrivere) se il lease non è più di questo worker, o se la nota è
        nel cestino o sparita: in quel caso il lavoro si cancella.
        """
        capture = notes.lock_capture(s, lease.capture_id)  # prima la nota, poi il lavoro
        job = jobs.held(s, lease.capture_id, lease.until, self.clock())
        if job is None:
            log.warning("nota %s: il lavoro non è più di questo worker (lease scaduto o "
                        "rimesso in coda), risultato scartato", lease.capture_id)
            return None
        if capture is None or capture.trashed_at is not None:
            jobs.drop(s, lease.capture_id)
            s.commit()
            log.info("nota %s nel cestino o eliminata: risultato scartato", lease.capture_id)
            return None
        return capture, job

    def _begin(self, lease: _Lease) -> tuple[str, str, str | None] | None:
        """(fase, percorso del WAV, testo) da elaborare, o None se non c'è niente da fare."""
        with self.sessionmaker() as s:
            held = self._hold(s, lease)
            if held is None:
                return None
            capture, job = held
            stage, text, now, until = job.stage, capture.transcript, self.clock(), lease.until
            if (stage == jobs.STAGE_TRANSCRIBE and text is not None
                    and "transcript" in capture.edited):
                stage = jobs.STAGE_ENRICH  # trascrizione corretta a mano: non si rifà
                until = jobs.advance(s, lease.capture_id, stage, now).locked_until
            if stage == jobs.STAGE_ENRICH and text is None:  # catalogo senza testo
                jobs.release(s, lease.capture_id, now, stage=jobs.STAGE_TRANSCRIBE)
                s.commit()
                return None
            if stage == jobs.STAGE_ENRICH and not text.strip():  # niente da arricchire
                jobs.mark_done(s, lease.capture_id, now)
                s.commit()
                return None
            s.commit()  # rilascia i lock prima delle chiamate ai provider
        lease.until = until
        return stage, capture.rel_path, text

    def _transcribe(self, lease: _Lease, rel_path: str, configs: list[ProviderConfig],
                    language: str) -> str | None:
        """Il testo da arricchire, o None se ci si ferma qui."""
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
            audio = AudioSource(self.archive.abs(rel_path), Path(tmp))
            config, transcript = chain.transcribe(configs, self.factory, audio, language,
                                                  self._on_call)
        now = self.clock()
        with self.sessionmaker() as s:
            held = self._hold(s, lease)
            if held is None:
                return None
            capture, _ = held
            note = notefile.apply_transcript(
                notes.note_from_capture(capture), transcript.text, provider=config.name,
                model=transcript.model, language=language, at=now)
            record = {"schema_version": AI_SCHEMA_VERSION,  # "Rielabora" lo sostituisce
                      "transcribe": _ai_record(config.name, transcript.model, now, transcript.raw)}
            self.archive.write_text(capture.rel_path, AI_SUFFIX, _dump(record))
            notes.write_note(s, self.archive, capture, note, self.tz)
            until = None
            if note.transcript:
                until = jobs.advance(s, lease.capture_id, jobs.STAGE_ENRICH, now).locked_until
            else:  # nessun parlato: niente arricchimento, resta il titolo di default
                jobs.mark_done(s, lease.capture_id, now)
            s.commit()
            rel_path = capture.rel_path
        if until is not None:
            lease.until = until
        log.info("trascritta %s con %s", rel_path, config.name)
        return note.transcript or None

    def _enrich(self, lease: _Lease, text: str, configs: list[ProviderConfig],
                language: str) -> None:
        config, enrichment = chain.enrich(configs, self.factory, text, language, self._on_call)
        now = self.clock()
        with self.sessionmaker() as s:
            held = self._hold(s, lease)
            if held is None:
                return
            capture, _ = held
            note = notefile.apply_enrichment(  # rilegge `edited` sotto il lock
                notes.note_from_capture(capture), enrichment.title, enrichment.summary,
                enrichment.tags, provider=config.name, model=enrichment.model, at=now)
            self._write_ai_section(capture.rel_path, "enrich",
                                   _ai_record(config.name, enrichment.model, now, enrichment.raw))
            notes.write_note(s, self.archive, capture, note, self.tz)
            jobs.mark_done(s, lease.capture_id, now)
            s.commit()
            rel_path = capture.rel_path
        log.info("elaborata %s con %s", rel_path, config.name)

    def _write_ai_section(self, rel_wav: str, section: str, record: dict) -> None:
        """Aggiorna solo la sezione di questa fase; un `.ai.json` illeggibile si sostituisce."""
        try:
            data = json.loads(self.archive.read_text(rel_wav, AI_SUFFIX))
        except FileNotFoundError:
            data = {}
        except ValueError:  # JSON rotto o non UTF-8
            log.warning("%s%s illeggibile: lo sostituisco", rel_wav, AI_SUFFIX)
            data = {}
        if not isinstance(data, dict):
            log.warning("%s%s senza un oggetto JSON: lo sostituisco", rel_wav, AI_SUFFIX)
            data = {}
        data["schema_version"] = AI_SCHEMA_VERSION
        data[section] = record
        self.archive.write_text(rel_wav, AI_SUFFIX, _dump(data))

    def _on_call(self, provider: str, audio_seconds: float) -> None:
        """Conteggio dell'utilizzo: se non si registra, la nota va avanti lo stesso."""
        try:
            with self.sessionmaker() as s:
                record_usage(s, provider, month_start(self.clock(), self.tz), audio_seconds)
                s.commit()
        except Exception as exc:  # noqa: BLE001 - effetto collaterale, mai bloccante
            log.warning("utilizzo di %s non registrato: %s", provider, type(exc).__name__)

    def _fail(self, lease: _Lease, error: str, *, permanent: bool) -> None:
        with self.sessionmaker() as s:
            if self._hold(s, lease) is None:
                return
            job = jobs.fail(s, lease.capture_id, self.clock(), error, permanent=permanent)
            s.commit()
            attempts, status = job.attempts, job.status
        log.warning("nota %s: %s (tentativo %d, ora %s)", lease.capture_id, error, attempts,
                    status)

    def _release(self, lease: _Lease) -> None:
        """Arresto: di nuovo in coda senza contare il tentativo; se non riesce, c'è il lease."""
        try:
            with self.sessionmaker() as s:
                if self._hold(s, lease) is None:
                    return
                jobs.release(s, lease.capture_id, self.clock())
                s.commit()
        except Exception as exc:  # noqa: BLE001 - si sta uscendo comunque
            log.warning("impossibile rimettere in coda %s (%s): lo riprenderà il lease",
                        lease.capture_id, type(exc).__name__)


def run_forever(worker, stop: threading.Event, interval: float = POLL_INTERVAL_S) -> None:
    log.info("worker avviato")
    try:
        while not stop.is_set():
            try:
                busy = worker.run_once()
            except SQLAlchemyError:
                log.exception("database non raggiungibile, riprovo tra poco")
                busy = False
            if not busy:
                stop.wait(interval)
    except Shutdown:
        pass
    log.info("worker fermato")


def install_signal_handlers(stop: threading.Event) -> None:
    """SIGTERM (docker stop) e SIGINT interrompono anche una chiamata HTTP in corso."""
    def handler(signum, frame):
        stop.set()
        raise Shutdown

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
