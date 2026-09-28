"""Navigazione del finder: anni, mesi, giorni, dettaglio, audio e file (spec §7)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import catalog
from ..models import Capture, WebSession
from .context import base_context, clean_device, page_context
from .deps import get_db, require_login
from .templating import MONTHS, templates

router = APIRouter()


def _capture(db: Session, capture_id: uuid.UUID) -> Capture:
    capture = catalog.get_capture(db, capture_id)
    if capture is None:
        raise HTTPException(404, "registrazione sconosciuta")
    return capture


@router.get("/browse")
def browse_root(request: Request, device: str | None = None, db: Session = Depends(get_db),
                session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session, clean_device(device))
    items = [(str(y), n, f"/browse/{y}{ctx['qs']}") for y, n in ctx["tree"]["years"]]
    return templates.TemplateResponse(request, "grid.html",
                                      ctx | {"crumbs": [("Archivio", None)], "items": items})


@router.get("/browse/{year}")
def browse_year(request: Request, year: int = Path(ge=1970, le=9999), device: str | None = None,
                db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session, clean_device(device), year)
    qs = ctx["qs"]
    items = [(MONTHS[m - 1], n, f"/browse/{year}/{m:02d}{qs}") for m, n in ctx["tree"]["months"]]
    crumbs = [("Archivio", f"/browse{qs}"), (str(year), None)]
    return templates.TemplateResponse(request, "grid.html", ctx | {"crumbs": crumbs, "items": items})


@router.get("/browse/{year}/{month}")
def browse_month(request: Request, year: int = Path(ge=1970, le=9999),
                 month: int = Path(ge=1, le=12), device: str | None = None,
                 db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session, clean_device(device), year, month)
    qs = ctx["qs"]
    items = [(str(d), n, f"/browse/{year}/{month:02d}/{d:02d}{qs}") for d, n in ctx["tree"]["days"]]
    crumbs = [("Archivio", f"/browse{qs}"), (str(year), f"/browse/{year}{qs}"),
              (MONTHS[month - 1], None)]
    return templates.TemplateResponse(request, "grid.html", ctx | {"crumbs": crumbs, "items": items})


@router.get("/browse/{year}/{month}/{day}")
def browse_day(request: Request, year: int = Path(ge=1970, le=9999),
               month: int = Path(ge=1, le=12), day: int = Path(ge=1, le=31),
               device: str | None = None, db: Session = Depends(get_db),
               session: WebSession = Depends(require_login)):
    try:
        the_day = date(year, month, day)
    except ValueError:
        raise HTTPException(404, "giorno inesistente") from None
    device = clean_device(device)
    ctx = page_context(request, db, session, device, year, month)
    qs = ctx["qs"]
    crumbs = [("Archivio", f"/browse{qs}"), (str(year), f"/browse/{year}{qs}"),
              (MONTHS[month - 1], f"/browse/{year}/{month:02d}{qs}"), (str(day), None)]
    return templates.TemplateResponse(request, "day.html", ctx | {
        "crumbs": crumbs, "day_num": day, "captures": catalog.list_day(db, the_day, device)})


@router.get("/captures/{capture_id}")
def capture_detail(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                   session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    files = request.app.state.archive.related_files(capture.rel_path)
    return templates.TemplateResponse(request, "_detail.html",
                                      base_context(request, db, session) | {"c": capture, "files": files})


@router.get("/captures/{capture_id}/audio")
def capture_audio(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    return FileResponse(request.app.state.archive.abs(capture.rel_path), media_type="audio/wav")


@router.get("/captures/{capture_id}/files/{name}")
def capture_file(request: Request, capture_id: uuid.UUID, name: str,
                 db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    archive = request.app.state.archive
    if name not in archive.related_files(capture.rel_path):
        raise HTTPException(404, "file sconosciuto")
    return FileResponse(archive.abs(capture.rel_path).parent / name, filename=name)
