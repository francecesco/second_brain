"""OTA: release firmware, manifest e binari per tipo di dispositivo (spec §9).

Pubblicare è sempre un comando esplicito: il manifest si genera dalla release corrente,
non esiste un file manifest da dimenticare con una versione sbagliata.
"""
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .devices import AuthError, authenticate_any, bearer_token
from .httputil import is_lan_request, public_base_url
from .models import FirmwareRelease
from .naming import DEFAULT_DEVICE_TYPE, is_valid_device_type

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
FILE_RE = re.compile(r"secondbrain-\d+\.\d+\.\d+\.bin")


class OtaError(ValueError):
    """Operazione non valida sulle release firmware."""


def release_filename(version: str) -> str:
    return f"secondbrain-{version}.bin"


def current_release(s: Session, type_: str) -> FirmwareRelease | None:
    return s.scalar(select(FirmwareRelease)
                    .where(FirmwareRelease.type == type_, FirmwareRelease.current.is_(True)))


def list_releases(s: Session) -> list[FirmwareRelease]:
    return list(s.scalars(select(FirmwareRelease)
                          .order_by(FirmwareRelease.type, FirmwareRelease.id)))


def publish(s: Session, firmware_dir: Path, src: Path, type_: str, version: str,
            now: datetime) -> FirmwareRelease:
    if not is_valid_device_type(type_):
        raise OtaError(f"tipo di dispositivo non valido: {type_!r}")
    if VERSION_RE.fullmatch(version) is None:
        raise OtaError(f"versione non nel formato X.Y.Z: {version!r}")
    existing = s.scalar(select(FirmwareRelease).where(
        FirmwareRelease.type == type_, FirmwareRelease.version == version))
    if existing is not None:
        raise OtaError(f"{type_} {version} già pubblicata")
    if not src.is_file():
        raise OtaError(f"file non trovato: {src}")
    dest_dir = firmware_dir / type_
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / release_filename(version)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    with open(dest, "rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    s.execute(update(FirmwareRelease).where(FirmwareRelease.type == type_)
              .values(current=False))
    release = FirmwareRelease(type=type_, version=version, file=dest.name, sha256=sha,
                              published_at=now, current=True)
    s.add(release)
    s.flush()
    return release


def rollback(s: Session, type_: str) -> FirmwareRelease:
    current = current_release(s, type_)
    if current is None:
        raise OtaError(f"nessuna release corrente per {type_}")
    previous = s.scalar(select(FirmwareRelease)
                        .where(FirmwareRelease.type == type_, FirmwareRelease.id < current.id)
                        .order_by(FirmwareRelease.id.desc()).limit(1))
    if previous is None:
        raise OtaError(f"nessuna release precedente a {current.version}")
    current.current = False
    s.flush()
    previous.current = True
    s.flush()
    return previous


router = APIRouter()


def require_device(request: Request) -> None:
    state = request.app.state
    with state.sessionmaker() as s:
        try:
            authenticate_any(s, bearer_token(request.headers),
                             state.settings.allow_unauthenticated_lan and is_lan_request(request))
        except AuthError as exc:
            raise HTTPException(exc.status, exc.detail) from None


def _manifest(request: Request, type_: str) -> dict:
    if not is_valid_device_type(type_):
        raise HTTPException(404, "tipo sconosciuto")
    with request.app.state.sessionmaker() as s:
        release = current_release(s, type_)
    if release is None:
        raise HTTPException(404, "nessuna release pubblicata")
    return {
        "version": release.version,
        "url": f"{public_base_url(request)}/firmware/{release.type}/{release.file}",
        "sha256": release.sha256,
    }


@router.get("/firmware/manifest.json", dependencies=[Depends(require_device)])
def default_manifest(request: Request) -> dict:
    return _manifest(request, DEFAULT_DEVICE_TYPE)


@router.get("/firmware/{type_}/manifest.json", dependencies=[Depends(require_device)])
def manifest(request: Request, type_: str) -> dict:
    return _manifest(request, type_)


@router.get("/firmware/{type_}/{filename}", dependencies=[Depends(require_device)])
def binary(request: Request, type_: str, filename: str) -> FileResponse:
    if not is_valid_device_type(type_) or FILE_RE.fullmatch(filename) is None:
        raise HTTPException(404, "file sconosciuto")
    with request.app.state.sessionmaker() as s:
        release = s.scalar(select(FirmwareRelease).where(
            FirmwareRelease.type == type_, FirmwareRelease.file == filename))
    path = request.app.state.settings.firmware_dir / type_ / filename
    if release is None or not path.is_file():
        raise HTTPException(404, "file sconosciuto")
    return FileResponse(path, media_type="application/octet-stream")
