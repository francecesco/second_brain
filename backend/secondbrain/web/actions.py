"""Azioni sulle registrazioni e pagine cestino, da sistemare, dispositivi (spec §7)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import catalog, library, ota
from ..devices import DeviceError, rename_device
from ..models import WebSession
from ..naming import day_dir
from .context import base_context, page_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

MIN_YEAR = 2000
router = APIRouter()


def _archive(request: Request):
    return request.app.state.archive


@router.post("/captures/{capture_id}/title")
def set_title_route(request: Request, capture_id: uuid.UUID, title: str = Form(""),
                    db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        capture = library.set_title(db, _archive(request), capture_id, title)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return templates.TemplateResponse(request, "_row.html",
                                      base_context(request, db, session) | {"c": capture})


@router.post("/captures/{capture_id}/recorded-at")
def correct_date_route(request: Request, capture_id: uuid.UUID, recorded_at: str = Form(""),
                       db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        local = datetime.fromisoformat(recorded_at)
    except ValueError:
        return PlainTextResponse("data/ora non valida", status_code=400)
    if local.tzinfo is not None or local.year < MIN_YEAR:
        return PlainTextResponse("data/ora non valida", status_code=400)
    try:
        capture = library.correct_recorded_at(db, _archive(request),
                                              request.app.state.settings.tz_archive,
                                              capture_id, local)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return HTMLResponse("", headers={"HX-Redirect": f"/browse/{day_dir(capture.day)}"})


@router.post("/captures/{capture_id}/trash")
def trash_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                session: WebSession = Depends(require_csrf)):
    try:
        library.trash_capture(db, _archive(request), capture_id, request.app.state.clock())
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return HTMLResponse("")  # htmx sostituisce la riga con niente


@router.get("/trash")
def trash_page(request: Request, db: Session = Depends(get_db),
               session: WebSession = Depends(require_login)):
    return templates.TemplateResponse(request, "trash.html", page_context(request, db, session) | {
        "crumbs": [("Cestino", None)], "captures": catalog.list_trashed(db),
        "retention_days": request.app.state.settings.trash_retention_days})


@router.post("/trash/{capture_id}/restore")
def restore_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_csrf)):
    try:
        library.restore_capture(db, _archive(request), capture_id)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return RedirectResponse("/trash", status_code=303)


@router.post("/trash/{capture_id}/delete")
def delete_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    try:
        library.delete_capture(db, _archive(request), capture_id)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.NotInTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return RedirectResponse("/trash", status_code=303)


@router.get("/fix")
def fix_page(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)):
    return templates.TemplateResponse(request, "fix.html", page_context(request, db, session) | {
        "crumbs": [("Da sistemare", None)], "captures": catalog.list_estimated(db),
        "show_day": True})


@router.get("/devices")
def devices_page(request: Request, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session)
    releases = {d.type: ota.current_release(db, d.type) for d in ctx["devices"]}
    return templates.TemplateResponse(request, "devices.html", ctx | {
        "crumbs": [("Dispositivi", None)], "releases": releases})


@router.post("/devices/{device_id}/name")
def rename_route(device_id: str, name: str = Form(""), db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    try:
        rename_device(db, device_id, name)
    except DeviceError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    db.commit()
    return RedirectResponse("/devices", status_code=303)
