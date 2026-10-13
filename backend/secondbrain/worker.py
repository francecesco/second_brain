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
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import httpx

from . import ha_store, homeassistant, jobs, notefile, notes, search, textnotes, vault_sync
from . import settings_store as store
from .ai import chain
from .ai.audio import AudioError, AudioSource
from .ai.base import ContentError, Hints
from .ai.registry import ProviderConfig
from .archive import AI_SUFFIX, Archive
from .clock import utcnow
from .models import Capture, Job
from .notefile import _edited
from .usage import month_start, record_usage
from .vault import Vault

KNOWN_TAGS = 40  # tag gia' usati passati all'arricchimento (spec tag e nomi §4)
log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
POLL_INTERVAL_S = 5.0
VAULT_CHECK_INTERVAL_S = 60.0   # modifiche da Obsidian (spec vault §7)
VAULT_CHECK_KEY = "vault_check"
HA_REFRESH_S = 300   # lettura di Home Assistant (spec Home Assistant e Oggi)
AI_SCHEMA_VERSION = 1
TEMP_PREFIX = "secondbrain-audio-"
IDLE_NO_KEY = "SETTINGS_KEY non impostata"
IDLE_BAD_KEY = "SETTINGS_KEY non valida"
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
    vault: Vault | None = None          # None solo nei test vecchi: la nota resta accanto al WAV
    public_url: str | None = None
    http: httpx.Client | None = None    # per Home Assistant; None = nessuna lettura
    _idle_reason: str | None = field(default=None, init=False)
    _last_vault_check: datetime | None = field(default=None, init=False)
    _last_ha_refresh: datetime | None = field(default=None, init=False)

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

    def maybe_check_vault(self) -> bool:
        """Quando e' libero, ogni VAULT_CHECK_INTERVAL_S: riprende le modifiche fatte in Obsidian."""
        if self.vault is None:
            return False
        now = self.clock()
        if (self._last_vault_check is not None
                and (now - self._last_vault_check).total_seconds() < VAULT_CHECK_INTERVAL_S):
            return False
        self._last_vault_check = now
        try:
            report = vault_sync.check(self.sessionmaker, self.vault, self.tz, self.public_url, now,
                                      archive=self.archive)
            notes_count = len(self.vault.list_notes())
        except OSError as exc:   # volume assente o in sola lettura: lo si vede in Impostazioni
            log.error("vault non leggibile (%s): %s", self.vault.root, exc)
            report, notes_count = vault_sync.VaultReport(problems=[f"vault non leggibile: {exc}"]), 0
        with self.sessionmaker() as s:
            store.set_setting(s, VAULT_CHECK_KEY, {"at": now.isoformat(), "notes": notes_count,
                                                   "changed": report.changed,
                                                   "problems": report.problems[:20]})
            s.commit()
        return True

    def maybe_refresh_homeassistant(self) -> bool:
        """Legge Home Assistant al primo giro, ogni HA_REFRESH_S o su richiesta della UI.

        True se ha provato a leggere (anche se e' fallita: l'errore sta nell'istantanea).
        """
        if self.http is None:
            return False
        now = self.clock()
        with self.sessionmaker() as s:
            config = ha_store.get_config(s)
            token = ha_store.get_token(s, self.box) if config else None
            if config is None or not token:
                return False
            due = (self._last_ha_refresh is None
                   or (now - self._last_ha_refresh).total_seconds() >= HA_REFRESH_S
                   or ha_store.refresh_requested_after(s, self._last_ha_refresh))
            if not due:
                return False
        self._last_ha_refresh = now
        local = now.astimezone(self.tz)
        end = datetime.combine(local.date() + timedelta(days=2), time.min, tzinfo=self.tz)
        try:
            reading = homeassistant.read(self.http, config, token, now, end)
            error = None
        except homeassistant.HaError as exc:  # il messaggio non contiene mai il token
            reading, error = None, str(exc)
            log.warning("Home Assistant: %s", error)
        with self.sessionmaker() as s:
            if reading is not None:
                ha_store.save_reading(s, reading, now)
            else:
                ha_store.save_error(s, error, now)
            s.commit()
        return True

    def _check(self, s: Session) -> tuple[str | None, list[ProviderConfig]]:
        if not self.box.available:
            return (IDLE_BAD_KEY if self.box.invalid else IDLE_NO_KEY), []
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
                text = self._transcribe(lease, rel_path, configs, language, self._hints())
                if text is None:
                    return
            # Indizi riletti dopo la trascrizione: i tag gia' usati non contano questa nota.
            self._enrich(lease, text, configs, language, self._hints(lease.capture_id),
                         tags_only=stage == jobs.STAGE_RETAG)
        except Shutdown:
            self._release(lease)
            raise
        except (ContentError, AudioError) as exc:
            self._fail(lease, str(exc), permanent=True)
        except chain.AllProvidersFailed as exc:
            self._fail(lease, str(exc), permanent=False)
        except FileNotFoundError as exc:  # WAV spostato a lavoro in corso: si riprova
            self._fail(lease, str(exc), permanent=False)
        except Exception as exc:  # noqa: BLE001 - disco pieno, DB, bug: si riprova con backoff
            log.exception("elaborazione di %s interrotta da un errore locale", capture_id)
            self._fail(lease, f"errore locale: {type(exc).__name__}", permanent=False)

    def _hints(self, capture_id: uuid.UUID | None = None) -> Hints:
        """Vocabolario e tag piu' usati (spec tag e nomi §4), letti a ogni lavoro: una
        modifica al vocabolario vale dalla nota successiva."""
        with self.sessionmaker() as s:
            known = tuple(tag for tag, _ in search.tag_counts(s, exclude=capture_id)[:KNOWN_TAGS])
            return Hints(names=store.vocabulary(s), known_tags=known)

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
            if stage == jobs.STAGE_TRANSCRIBE and textnotes.is_text_capture(capture):
                # Nota scritta in fase transcribe (es. dopo rescan senza `.md`, o con un `.md`
                # la cui riga `edited` e' stata tolta in Obsidian): mai il provider audio
                # (spec note scritte §5). Il testo gia' in catalogo vince (puo' avere
                # correzioni), altrimenti il `.txt`.
                source = text if (text or "").strip() else textnotes.read_source(self.archive, capture)
                note = replace(notes.note_from_capture(capture), transcript=source,
                               edited=_edited((*capture.edited, "transcript")),
                               language=capture.language or store.get_language(s))
                notes.write_note(s, self.archive, capture, note, self.tz, vault=self.vault,
                                 public_url=self.public_url)
                text, stage = note.transcript, jobs.STAGE_ENRICH
                until = jobs.advance(s, lease.capture_id, stage, now).locked_until
            if (stage == jobs.STAGE_TRANSCRIBE and text is not None
                    and "transcript" in capture.edited):
                stage = jobs.STAGE_ENRICH  # trascrizione corretta a mano: non si rifà
                until = jobs.advance(s, lease.capture_id, stage, now).locked_until
            if stage == jobs.STAGE_RETAG and not (text or "").strip():  # niente da ritaggare
                jobs.mark_done(s, lease.capture_id, now)
                s.commit()
                return None
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
                    language: str, hints: Hints) -> str | None:
        """Il testo da arricchire, o None se ci si ferma qui."""
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
            audio = AudioSource(self.archive.abs(rel_path), Path(tmp))
            config, transcript = chain.transcribe(configs, self.factory, audio, language,
                                                  self._on_call, hints)
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
            notes.write_note(s, self.archive, capture, note, self.tz, vault=self.vault,
                             public_url=self.public_url)
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
                language: str, hints: Hints, *, tags_only: bool = False) -> None:
        config, enrichment = chain.enrich(configs, self.factory, text, language, self._on_call,
                                          hints)
        now = self.clock()
        with self.sessionmaker() as s:
            held = self._hold(s, lease)
            if held is None:
                return
            capture, _ = held
            if capture.transcript is None:  # catalogo azzerato nel frattempo (es. rescan)
                jobs.release(s, lease.capture_id, now, stage=jobs.STAGE_TRANSCRIBE)
                s.commit()
                log.warning("nota %s senza trascrizione nel catalogo: di nuovo da trascrivere",
                            lease.capture_id)
                return
            if tags_only:  # retag: titolo, riassunto e provenienza restano
                note = notefile.apply_tags(notes.note_from_capture(capture), enrichment.tags)
            else:
                note = notefile.apply_enrichment(  # rilegge `edited` sotto il lock
                    notes.note_from_capture(capture), enrichment.title, enrichment.summary,
                    enrichment.tags, provider=config.name, model=enrichment.model, at=now)
            self._write_ai_section(capture.rel_path, "retag" if tags_only else "enrich",
                                   _ai_record(config.name, enrichment.model, now, enrichment.raw))
            notes.write_note(s, self.archive, capture, note, self.tz, vault=self.vault,
                             public_url=self.public_url)
            jobs.mark_done(s, lease.capture_id, now)
            s.commit()
            rel_path = capture.rel_path
        log.info("%s %s con %s", "ritaggata" if tags_only else "elaborata", rel_path, config.name)

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
            log.warning("utilizzo di %s non registrato: %s", provider, type(exc).__name__,
                        exc_info=True)

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
                        lease.capture_id, type(exc).__name__, exc_info=True)


def run_forever(worker, stop: threading.Event, interval: float = POLL_INTERVAL_S) -> None:
    log.info("worker avviato")
    try:
        while not stop.is_set():
            try:
                busy = worker.run_once()
            except SQLAlchemyError:
                log.exception("database non raggiungibile, riprovo tra poco")
                busy = False
            try:
                worker.maybe_refresh_homeassistant()
            except Exception:  # noqa: BLE001 - Home Assistant non deve fermare il worker
                log.exception("lettura di Home Assistant interrotta, riprovo al prossimo giro")
            if not busy:
                try:
                    worker.maybe_check_vault()
                except SQLAlchemyError:
                    log.exception("database non raggiungibile durante il controllo del vault")
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
