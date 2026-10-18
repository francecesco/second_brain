"""Azioni sulle registrazioni e pagine cestino, da sistemare, dispositivi (spec §7)."""
import uuid
from urllib.parse import parse_qs, quote, urlsplit
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import catalog, library, ota
from ..devices import DeviceError, rename_device
from ..models import WebSession
from ..naming import day_dir, is_valid_device_type
from .context import base_context, clean_device, page_context, today_local
from .groups import day_groups, due_groups
from ..notefile import Action, clean_action_text, parse_due
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
    # La riga esce dalla lista in cui stava (archivio e "Da fare" se spuntata, "Completati"
    # se tolta la spunta): la risposta e' vuota per la riga, il resto e' out-of-band.
    return templates.TemplateResponse(request, "_toggle_done.html", _list_context(request, db, session))


def _list_context(request: Request, db: Session, session: WebSession) -> dict:
    """Contesto per gli aggiornamenti out-of-band di una spunta, come li vede la pagina da
    cui arriva il clic: menu, albero e, su "Da fare", tessere, contatore e In scadenza."""
    section, device, date_parts = _current_page(request)
    year, month, day = (date_parts + [None] * 3)[:3]
    ctx = page_context(request, db, session, device, year, month) | {"section": section, "day_num": day}
    if section == "todo":  # spec UI responsive §2.6, azioni §5
        today = today_local(request)
        ctx["tiles"] = catalog.todo_tiles(db, device, today)
        ctx["due"] = due_groups(catalog.open_actions(db, device), today)
    return ctx


def _leaves_list(section: str | None, capture) -> bool:
    """Dopo una spunta la nota non sta più nella lista della pagina?"""
    if section in ("todo", "browse"):
        return capture.done_at is not None
    if section == "done":
        return capture.done_at is None
    return False


@router.post("/captures/{capture_id}/actions/{action_id}/{state}")
def action_done_route(request: Request, capture_id: uuid.UUID, action_id: str, state: str,
                      db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    """Spunta di un'azione (spec azioni §5): la lista delle azioni della riga si ridisegna; se
    la nota esce dalla lista della pagina (tutte fatte, o una riaperta in Completati) sparisce
    tutta la riga."""
    if state not in ("done", "undone"):
        raise HTTPException(404, "azione sconosciuta")
    state_app = request.app.state
    try:
        capture = library.set_action_done(db, _archive(request), state_app.settings.tz_archive,
                                          capture_id, action_id, state == "done", state_app.clock(),
                                          vault=state_app.vault, public_url=state_app.settings.public_url)
    except library.NotFound:
        raise HTTPException(404, "azione sconosciuta") from None
    except (library.InTrash, library.NotProcessed) as exc:
        return PlainTextResponse(str(exc), status_code=409)
    ctx = _list_context(request, db, session) | {"c": capture}
    gone = _leaves_list(ctx["section"], capture)
    response = templates.TemplateResponse(request, "_actions_changed.html", ctx | {"gone": gone})
    if gone:
        response.headers["HX-Retarget"] = f"#row-{capture.id}"
        response.headers["HX-Reswap"] = "outerHTML"
    return response


@router.post("/captures/{capture_id}/actions")
async def actions_edit_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                             session: WebSession = Depends(require_csrf)):
    """Azioni riscritte dal dettaglio: righe con id (vuoto per le nuove), testo e data."""
    form = await request.form()
    ids, texts, dues = form.getlist("id"), form.getlist("text"), form.getlist("due")
    if not (len(ids) == len(texts) == len(dues)):
        return PlainTextResponse("righe delle azioni incomplete", status_code=400)
    actions = [Action(str(i), str(t), parse_due(str(d))) for i, t, d in zip(ids, texts, dues)
               if clean_action_text(str(t))]
    state_app = request.app.state
    try:
        capture = library.edit_actions(db, _archive(request), state_app.settings.tz_archive,
                                       capture_id, actions, state_app.clock(), vault=state_app.vault,
                                       public_url=state_app.settings.public_url)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except (library.InTrash, library.NotProcessed) as exc:
        return PlainTextResponse(str(exc), status_code=409)
    ctx = _list_context(request, db, session) | {"c": capture, "field": "actions", "editing": False}
    return templates.TemplateResponse(request, "_actions_saved.html", ctx)


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
        "crumbs": [("Da fare", None)], "groups": groups, "tiles": catalog.todo_tiles(db, device, today),
        "due": due_groups(catalog.open_actions(db, device), today)})


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
    # card Firmware per ogni tipo che ha un dispositivo o una release (il tipo `web` è l'app
    # stessa: niente firmware)
    all_releases = ota.list_releases(db)
    types = sorted({d.type for d in ctx["devices"]} | {r.type for r in all_releases}
                   - {ota.WEB_DEVICE_TYPE})
    firmware = []
    for t in types:
        rels = [r for r in all_releases if r.type == t]
        current = next((r for r in rels if r.current), None)
        # "Torna alla precedente" vale solo se c'è una release pubblicata prima della corrente
        firmware.append({"type": t, "releases": rels[::-1],
                         "can_rollback": current is not None and any(r.id < current.id for r in rels)})
    q = request.query_params
    return templates.TemplateResponse(request, "devices.html", ctx | {
        "crumbs": [("Dispositivi", None)], "releases": releases, "firmware": firmware,
        "published_now": q.get("pubblicata"), "current_now": q.get("corrente"),
        "fw_error": q.get("errore")})


def _fw_back(**params: str) -> RedirectResponse:
    query = "&".join(f"{k}={quote(v)}" for k, v in params.items())
    return RedirectResponse(f"/devices?{query}#firmware", status_code=303)


@router.post("/devices/firmware/{type_}")
async def publish_firmware_route(request: Request, type_: str, file: UploadFile | None = File(None),
                                 db: Session = Depends(get_db),
                                 session: WebSession = Depends(require_csrf)):
    """Upload del binario dalla card Firmware: la versione è quella nell'intestazione."""
    if not is_valid_device_type(type_):
        raise HTTPException(404, "tipo sconosciuto")
    if file is None:
        return _fw_back(errore="scegli il file .bin del firmware")
    state = request.app.state
    data = await file.read(state.settings.max_upload_bytes + 1)
    if len(data) > state.settings.max_upload_bytes:
        return _fw_back(errore="file troppo grande")
    try:
        release = ota.publish_bytes(db, state.settings.firmware_dir, data, type_, state.clock())
    except ota.OtaError as exc:
        return _fw_back(errore=str(exc))
    db.commit()
    return _fw_back(pubblicata=release.version)


@router.post("/devices/firmware/{type_}/rollback")
def rollback_firmware_route(request: Request, type_: str, db: Session = Depends(get_db),
                            session: WebSession = Depends(require_csrf)):
    if not is_valid_device_type(type_):
        raise HTTPException(404, "tipo sconosciuto")
    try:
        release = ota.rollback(db, type_)
    except ota.OtaError as exc:
        return _fw_back(errore=str(exc))
    db.commit()
    return _fw_back(corrente=release.version)


@router.post("/devices/{device_id}/name")
def rename_route(device_id: str, name: str = Form(""), db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    try:
        rename_device(db, device_id, name)
    except DeviceError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    db.commit()
    return RedirectResponse("/devices", status_code=303)
