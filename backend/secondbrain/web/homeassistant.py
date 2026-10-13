"""Card "Connessione Home Assistant" delle Impostazioni.

Il token è in sola scrittura come le chiavi dei provider: il campo è sempre vuoto e il
token non torna mai al browser, nemmeno nei messaggi d'errore. Le letture vere le fa il
worker: qui si salvano indirizzo, token e scelte e si chiede un aggiornamento.
"""
import re
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from markupsafe import escape
from sqlalchemy.orm import Session

from .. import ha_store
from .. import homeassistant as ha
from .. import settings_store as store
from ..models import WebSession
from .deps import get_db, require_csrf, require_login
from .settings import ha_view, render_settings
from .templating import templates

router = APIRouter()

ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
NO_CONNECTION = "Nessuna connessione salvata: inserisci URL e token"
NO_SETTINGS_KEY = "SETTINGS_KEY non impostata: il token non si può salvare né leggere"
CHOICES_FIRST = "Prima salva URL e token di Home Assistant"


@dataclass(frozen=True)
class Choice:
    entity_id: str
    name: str
    checked: bool
    missing: bool = False      # scelta salvata che Home Assistant non elenca più
    label: str = ""


def _back() -> RedirectResponse:
    return RedirectResponse("/settings#homeassistant", status_code=303)


def _result(ok: bool, message: str) -> HTMLResponse:
    css = "ok" if ok else "ko"
    return HTMLResponse(f'<span class="test-result {css}" role="status">{escape(message)}</span>')


def _card_error(request: Request, db: Session, session: WebSession, message: str,
                status_code: int, url: str | None = None):
    """La pagina con l'errore nella card; niente di quanto inviato resta nella sessione."""
    db.rollback()
    view = ha_view(db, request.app.state.box, url=url, message=message)
    return render_settings(request, db, session, ha=view, status_code=status_code)


def _connection(request: Request, db: Session) -> tuple[ha.HaConfig, str] | str:
    """Configurazione e token salvati, o il messaggio che spiega cosa manca."""
    box = request.app.state.box
    config = ha_store.get_config(db)
    if not box.available:
        return NO_SETTINGS_KEY
    if config is None or store.get_setting(db, ha_store.TOKEN_KEY, None) is None:
        return NO_CONNECTION
    token = ha_store.get_token(db, box)
    if token is None:
        return store.UNREADABLE_KEY
    return config, token


@router.post("/settings/homeassistant")
def save_connection(request: Request, url: str = Form(""), token: str = Form(""),
                    db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        ha_store.save_connection(db, request.app.state.box, url, token)
    except ha.HaError as exc:
        return _card_error(request, db, session, str(exc), 400, url=url)
    except store.NoSettingsKey:
        return _card_error(request, db, session, NO_SETTINGS_KEY, 409, url=url)
    ha_store.request_refresh(db, request.app.state.clock())
    db.commit()
    return _back()


@router.post("/settings/homeassistant/test")
def probe(request: Request, db: Session = Depends(get_db),
          session: WebSession = Depends(require_csrf)) -> HTMLResponse:
    """"Prova": chiama `/api/` con URL e token salvati; mai il token nella risposta."""
    found = _connection(request, db)
    if isinstance(found, str):
        return _result(False, found)
    config, token = found
    try:
        return _result(True, ha.test(request.app.state.http, config.url, token))
    except ha.HaError as exc:
        return _result(False, str(exc))


def _choices(entities: list[ha.Entity], saved: dict[str, str]) -> list[Choice]:
    """Entità di un gruppo, spuntate se salvate; le salvate non più elencate in fondo."""
    listed = {e.entity_id for e in entities}
    items = [Choice(e.entity_id, e.name, e.entity_id in saved, label=saved.get(e.entity_id, ""))
             for e in entities]
    items += [Choice(eid, eid, True, missing=True, label=label)
              for eid, label in saved.items() if eid not in listed]
    return items


@router.get("/settings/homeassistant/entities")
def entities(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)) -> HTMLResponse:
    found = _connection(request, db)
    if isinstance(found, str):
        return _result(False, found)
    config, token = found
    try:
        listed = ha.list_entities(request.app.state.http, config.url, token)
    except ha.HaError as exc:
        return _result(False, str(exc))
    chosen = list(config.sensors)[:ha_store.MAX_SENSOR_ROWS]
    listed_sensors = {e.entity_id for e in listed["sensor"]}
    rows = [{"n": i + 1, "entity_id": chosen[i].entity_id if i < len(chosen) else "",
             "label": (chosen[i].label or "") if i < len(chosen) else "",
             "missing": i < len(chosen) and chosen[i].entity_id not in listed_sensors}
            for i in range(ha_store.MAX_SENSOR_ROWS)]
    shopping = {config.shopping_list: ""} if config.shopping_list else {}
    return templates.TemplateResponse(request, "_homeassistant_entities.html", {
        "csrf": session.csrf_token, "max_label": ha_store.MAX_LABEL_LEN,
        "calendars": _choices(listed["calendar"], {c: "" for c in config.calendars}),
        "sensor_options": listed["sensor"], "sensor_rows": rows,
        "lists": _choices(listed["todo"], shopping), "no_list": not config.shopping_list})


def _entity_ids(values: list, domain: str) -> list[str] | None:
    """Entity_id del dominio atteso, senza doppioni; None se uno non è valido."""
    out: list[str] = []
    for value in values:
        if not (isinstance(value, str) and ENTITY_ID.match(value)
                and value.startswith(domain + ".")):
            return None
        if value not in out:
            out.append(value)
    return out


@router.post("/settings/homeassistant/choices")
async def save_choices(request: Request, db: Session = Depends(get_db),
                       session: WebSession = Depends(require_csrf)):
    data = await request.form()
    calendars = _entity_ids(data.getlist("calendars"), "calendar")
    picked = [(data.get(f"sensor_{n}"), data.get(f"label_{n}"))
              for n in range(1, ha_store.MAX_SENSOR_ROWS + 1)]
    sensors = _entity_ids([eid for eid, _ in picked if eid], "sensor")
    shopping = data.get("shopping_list") or ""
    lists = _entity_ids([shopping] if shopping else [], "todo")
    if calendars is None or sensors is None or lists is None:
        return PlainTextResponse("entità non valida", status_code=400)
    if ha_store.get_config(db) is None:
        return _card_error(request, db, session, CHOICES_FIRST, 409)
    # righe in ordine, la prima occorrenza di un sensore vince (con la sua etichetta)
    chosen: dict[str, str | None] = {}
    for eid, label in picked:
        if eid and eid not in chosen:
            chosen[eid] = label if isinstance(label, str) else None
    ha_store.save_choices(db, calendars, [ha.SensorChoice(e, lb) for e, lb in chosen.items()],
                          lists[0] if lists else None)
    ha_store.request_refresh(db, request.app.state.clock())
    db.commit()
    return _back()


@router.post("/settings/homeassistant/refresh")
def refresh(request: Request, db: Session = Depends(get_db),
            session: WebSession = Depends(require_csrf)):
    ha_store.request_refresh(db, request.app.state.clock())
    db.commit()
    return _back()


@router.post("/settings/homeassistant/delete")
def disconnect(db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    ha_store.clear(db)
    db.commit()
    return _back()
