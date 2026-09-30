"""Campi AI nel finder: stato della riga, modifica inline, "Rielabora" (spec AI §10)."""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from .. import catalog, jobs, library
from ..models import Capture, WebSession
from ..notefile import EDITABLE_FIELDS
from .context import base_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

HTMX_STOP_POLLING = 286  # con questo codice htmx smette di ricaricare l'elemento

router = APIRouter()


def _field(field: str) -> str:
    if field not in EDITABLE_FIELDS:
        raise HTTPException(404, "campo sconosciuto")
    return field


def _capture(db: Session, capture_id: uuid.UUID) -> Capture:
    capture = catalog.get_capture(db, capture_id)
    if capture is None:
        raise HTTPException(404, "registrazione sconosciuta")
    return capture


@router.get("/captures/{capture_id}/head")
def row_head(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)):
    capture = catalog.get_capture(db, capture_id)
    if capture is None or capture.trashed_at is not None:
        return Response(status_code=HTMX_STOP_POLLING)
    return templates.TemplateResponse(request, "_row_head.html",
                                      base_context(request, db, session) | {"c": capture})


@router.get("/captures/{capture_id}/field/{field}")
def field_view(request: Request, capture_id: uuid.UUID, field: str, edit: bool = False,
               db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    return templates.TemplateResponse(request, "_ai_field.html", base_context(request, db, session) | {
        "c": capture, "field": _field(field), "editing": edit and capture.trashed_at is None})


@router.post("/captures/{capture_id}/field/{field}")
def field_save(request: Request, capture_id: uuid.UUID, field: str, value: str = Form(""),
               db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    field = _field(field)
    state = request.app.state
    try:
        capture = library.edit_ai_field(db, state.archive, state.settings.tz_archive, capture_id,
                                        field, value)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    except library.NotProcessed as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return templates.TemplateResponse(request, "_field_saved.html", base_context(request, db, session) | {
        "c": capture, "field": field, "editing": False, "oob": True})


@router.post("/captures/{capture_id}/reprocess")
def reprocess_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                    session: WebSession = Depends(require_csrf)):
    try:
        library.reprocess(db, capture_id, request.app.state.clock())
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    except jobs.JobRunning:
        return PlainTextResponse("la nota è in elaborazione proprio ora", status_code=409)
    db.expire_all()
    capture = _capture(db, capture_id)
    files = request.app.state.archive.related_files(capture.rel_path)
    return templates.TemplateResponse(request, "_reprocess.html", base_context(request, db, session) | {
        "c": capture, "files": files, "oob": True})
