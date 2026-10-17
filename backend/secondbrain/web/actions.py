"""Azioni sulle registrazioni e pagine cestino, da sistemare, dispositivi (spec §7)."""
import uuid
from urllib.parse import parse_qs, urlsplit
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import catalog, library, ota
from ..devices import DeviceError, rename_device
from ..models import WebSession
from ..naming import day_dir
from .context import base_context, clean_device, page_context, today_local
from .groups import day_groups
from .deps import get_db, require_csrf, require_login
from .templating import templates

MIN_YEAR = 2000
router = APIRouter()


def _archive(request: Request):
    return request.app.state.archive


def _vault_kw(request: Request) -> dict:
    """Vault, fuso e URL pubblico per le operazioni che toccano la nota nel vault."""
    state = request.app.state
    return {"vault": state.vault, "tz": state.settings.tz_archive, "public_url": state.settings.public_url}


@router.post("/captures/{capture_id}/title")
def set_title_route(request: Request, capture_id: uuid.UUID, title: str = Form(""),
                    db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        capture = library.set_title(db, _archive(request), capture_id, title, **_vault_kw(request))
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return templates.TemplateResponse(request, "_row.html",
                                      base_context(request, db, session) | {"c": capture})


def _current_page(request: Request) -> tuple[str | None, str | None, list[int]]:
    """Sezione, dispositivo e data (anno, mese, giorno) della pagina da cui arriva il clic:
    contatori del menu e albero si ridisegnano come li vede quella pagina."""
    url = urlsplit(request.headers.get("hx-current-url", ""))
    parts = [p for p in url.path.split("/") if p]
    section = parts[0] if parts else None
    date_parts = [int(p) for p in parts[1:4] if p.isdigit()] if section == "browse" else []
    return section, clean_device(parse_qs(url.query).get("device", [None])[0]), date_parts


def _set_done(request: Request, capture_id: uuid.UUID, done: bool, db: Session, session: WebSession):
    try:
        library.set_done(db, _archive(request), capture_id, done, request.app.state.clock(),
                         **_vault_kw(request))
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    section, device, date_parts = _current_page(request)
    year, month, day = (date_parts + [None] * 3)[:3]
    ctx = page_context(request, db, session, device, year, month) | {"section": section, "day_num": day}
    if section == "todo":  # le tessere di "Da fare" seguono la spunta (spec UI responsive §2.6)
        ctx["tiles"] = catalog.todo_tiles(db, device, today_local(request))
    # La riga esce dalla lista in cui stava (archivio e "Da fare" se spuntata, "Completati"
    # se tolta la spunta): la risposta e' vuota per la riga, il resto e' out-of-band.
    return templates.TemplateResponse(request, "_toggle_done.html", ctx)


@router.post("/captures/{capture_id}/done")
def done_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
               session: WebSession = Depends(require_csrf)):
    return _set_done(request, capture_id, True, db, session)


@router.post("/captures/{capture_id}/undone")
def undone_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    return _set_done(request, capture_id, False, db, session)


@router.get("/todo")
def todo_page(request: Request, device: str | None = None, db: Session = Depends(get_db),
              session: WebSession = Depends(require_login)):
    device = clean_device(device)
    today = today_local(request)
    groups = day_groups(catalog.list_todo(db, device), lambda c: c.day, today)
    return templates.TemplateResponse(request, "todo.html", page_context(request, db, session, device) | {
        "crumbs": [("Da fare", None)], "groups": groups, "tiles": catalog.todo_tiles(db, device, today)})


@router.get("/done")
def done_page(request: Request, device: str | None = None, db: Session = Depends(get_db),
              session: WebSession = Depends(require_login)):
    device = clean_device(device)
    tz = request.app.state.settings.tz_archive
    groups = day_groups(catalog.list_done(db, device), lambda c: c.done_at.astimezone(tz).date(),
                        today_local(request))
    return templates.TemplateResponse(request, "done.html", page_context(request, db, session, device) | {
        "crumbs": [("Completati", None)], "groups": groups})


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
                                              capture_id, local, vault=request.app.state.vault,
                                              public_url=request.app.state.settings.public_url)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return HTMLResponse("", headers={"HX-Redirect": f"/browse/{day_dir(capture.day)}"})


@router.post("/captures/{capture_id}/trash")
def trash_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                session: WebSession = Depends(require_csrf)):
    try:
        library.trash_capture(db, _archive(request), capture_id, request.app.state.clock(),
                              **_vault_kw(request))
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
        library.restore_capture(db, _archive(request), capture_id, **_vault_kw(request))
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return RedirectResponse("/trash", status_code=303)


@router.post("/trash/{capture_id}/delete")
def delete_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    try:
        library.delete_capture(db, _archive(request), capture_id, vault=request.app.state.vault)
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
