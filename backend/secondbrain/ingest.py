"""POST /captures: ricezione delle registrazioni dai dispositivi (spec §6)."""
import errno
import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

from . import catalog, naming
from .archive import Archive
from .devices import AuthError, DeviceMeta, authenticate, bearer_token, record_seen
from .httputil import is_lan_request
from .models import Capture
from .sidecar import capture_to_sidecar
from .wav import HEADER_PROBE_BYTES, WavError, parse_wav_header

log = logging.getLogger(__name__)
router = APIRouter()


class Reject(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _commit(session: Session) -> None:
    session.commit()


def _content_length(headers: Headers, maximum: int) -> int:
    raw = headers.get("content-length")
    if raw is None:
        raise Reject(411, "Content-Length obbligatorio")
    try:
        length = int(raw)
    except ValueError:
        raise Reject(400, "Content-Length non valido") from None
    if length > maximum:
        raise Reject(413, f"file oltre {maximum} byte")
    return length


async def _receive(request: Request, archive: Archive, maximum: int) -> tuple[Path, int, str]:
    tmp = archive.new_incoming()
    hasher = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise Reject(413, f"file oltre {maximum} byte")
                hasher.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, size, hasher.hexdigest()


def _authenticate(state, token: str | None, device_id: str, meta: DeviceMeta,
                  now: datetime, allow_unauth: bool) -> None:
    with state.sessionmaker() as s:
        device = authenticate(s, token, device_id, allow_unauth, now)
        record_seen(device, meta, now)
        s.commit()


def _store(state, device_id: str, capture_id: str, ts_header: str | None, meta: DeviceMeta,
           tmp: Path, size: int, sha: str, now: datetime) -> tuple[int, dict]:
    with open(tmp, "rb") as f:
        head = f.read(HEADER_PROBE_BYTES)
    try:
        info = parse_wav_header(head, size)
    except WavError as exc:
        raise Reject(422, f"WAV non valido: {exc}") from None
    tz = state.settings.tz_archive
    archive: Archive = state.archive
    with state.sessionmaker() as s:
        previous = catalog.find_captures(s, device_id, capture_id)
        for p in previous:
            if p.sha256 == sha:
                return 409, {"id": str(p.id), "status": "duplicate"}
        if previous:
            log.warning("%s di %s già presente con contenuto diverso: archiviata come nuova",
                        capture_id, device_id)
        recorded_at, estimated = naming.resolve_recorded_at(ts_header, now)
        day = naming.local_day(recorded_at, tz)
        dir_rel = naming.day_dir(day)
        base = archive.reserve_base(dir_rel, naming.base_name(recorded_at, tz, device_id))
        capture = Capture(
            id=uuid.uuid4(), device_id=device_id, capture_id=capture_id,
            recorded_at=recorded_at, date_estimated=estimated, received_at=now, day=day,
            rel_path=f"{dir_rel}/{base}.wav", title=None, duration_s=info.duration_s,
            size_bytes=size, sha256=sha, firmware_version=meta.firmware_version,
            battery_pct=meta.battery_pct, battery_v=meta.battery_v,
            power_source=meta.power_source, trashed_at=None,
        )
        rel = archive.commit_capture(tmp, dir_rel, base, capture_to_sidecar(capture))
        s.add(capture)
        try:
            _commit(s)
        except Exception:
            # Senza riga il device ritenterà: i file non devono restare, o nascerebbe un doppione.
            archive.delete(rel)
            raise
    log.info("archiviata %s (%s, %d byte)", rel, capture_id, size)
    return 201, {"id": str(capture.id), "path": rel, "status": "accepted"}


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


@router.post("/captures")
async def post_capture(request: Request) -> JSONResponse:
    state = request.app.state
    headers = request.headers
    tmp: Path | None = None
    try:
        now = state.clock()
        device_id = headers.get("x-device-id", "")
        meta = DeviceMeta.from_headers(headers)
        allow_unauth = state.settings.allow_unauthenticated_lan and is_lan_request(request)
        await run_in_threadpool(_authenticate, state, bearer_token(headers), device_id, meta,
                                now, allow_unauth)
        capture_id = headers.get("x-capture-id", "")
        if not naming.is_valid_capture_id(capture_id):
            raise Reject(400, "X-Capture-Id mancante o non valido")
        maximum = state.settings.max_upload_bytes
        length = _content_length(headers, maximum)
        tmp, size, sha = await _receive(request, state.archive, maximum)
        if size != length:
            raise Reject(400, f"ricevuti {size} byte su {length} dichiarati")
        status, body = await run_in_threadpool(
            _store, state, device_id, capture_id, headers.get("x-capture-ts"), meta,
            tmp, size, sha, now)
        return JSONResponse(body, status_code=status)
    except (Reject, AuthError) as exc:
        return _error(exc.status, exc.detail)
    except ClientDisconnect:
        log.warning("upload interrotto dal client")
        return _error(400, "upload interrotto")
    except OperationalError:
        log.exception("database non raggiungibile durante la ricezione")
        return _error(503, "database non disponibile")
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            log.error("disco pieno durante la ricezione")
            return _error(507, "spazio su disco esaurito")
        raise
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)  # già spostato se archiviato
