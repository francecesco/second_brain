"""Pagina delle impostazioni dell'elaborazione AI (spec AI §8).

La chiave API è in sola scrittura: il campo è sempre vuoto, dopo il salvataggio si vede
solo mascherata, e non arriva mai al browser in nessuna risposta.
"""
from dataclasses import dataclass

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from markupsafe import escape
from sqlalchemy.orm import Session

from .. import ha_store, jobs
from .. import settings_store as store
from ..ai.base import CheckResult, redact
from ..ai.registry import PROVIDER_SPECS, ProviderConfig, ProviderSpec, build_provider
from ..homeassistant import HaConfig
from ..languages import LANGUAGES
from ..models import AiProvider, WebSession
from ..usage import month_start, usage_for_month
from .context import page_context, text_model_notice
from .deps import get_db, require_csrf, require_login
from .templating import templates

SECONDS_PER_MINUTE = 60
MINUTES_DECIMALS = 1  # cifre dopo la virgola dei minuti d'audio mostrati in pagina
NO_SETTINGS_KEY = "SETTINGS_KEY non impostata: le chiavi API non si possono salvare"

router = APIRouter()
templates.env.globals["MINUTES_DECIMALS"] = MINUTES_DECIMALS


@dataclass(frozen=True)
class ProviderView:
    row: AiProvider
    spec: ProviderSpec
    key_status: str
    minutes: float
    calls: int


def _spec(name: str) -> ProviderSpec:
    spec = PROVIDER_SPECS.get(name)
    if spec is None:
        raise HTTPException(404, "provider sconosciuto")
    return spec


def _back() -> RedirectResponse:
    return RedirectResponse("/settings", status_code=303)


def _vault_check(db: Session) -> dict | None:
    """Esito dell'ultimo controllo del vault fatto dal worker, con `at` come datetime."""
    data = store.get_setting(db, "vault_check", None)
    if not isinstance(data, dict) or not data.get("at"):
        return None
    try:
        return data | {"at": datetime.fromisoformat(str(data["at"]))}
    except ValueError:
        return None


@dataclass(frozen=True)
class HaView:
    """Card di Home Assistant: configurazione, stato del token, ultima lettura."""
    config: HaConfig | None
    token_status: str
    snapshot: ha_store.Snapshot
    error: str | None     # ultimo errore della lettura, ripulito
    url: str              # valore del campo: quello salvato o quello appena inviato
    message: str | None   # errore dell'ultimo invio della card


def ha_view(db: Session, box: store.SecretBox, url: str | None = None,
            message: str | None = None) -> HaView:
    config = ha_store.get_config(db)
    snapshot = ha_store.get_snapshot(db)
    token = ha_store.get_token(db, box)
    error = redact(snapshot.error, [token] if token else []) if snapshot.error else None
    status = ha_store.token_status(db, box)
    return HaView(config, "nessun token" if status == store.NO_KEY else status, snapshot, error,
                  url if url is not None else (config.url if config else ""), message)


def render_settings(request: Request, db: Session, session: WebSession, queued: int | None = None,
                    ha: HaView | None = None, status_code: int = 200):
    state = request.app.state
    now = state.clock()
    rows = store.ensure_providers(db, now)
    db.commit()
    usage = usage_for_month(db, month_start(now, state.settings.tz_archive))
    views = []
    for row in rows:
        used = usage.get(row.name)
        views.append(ProviderView(
            row=row, spec=PROVIDER_SPECS[row.name], key_status=store.key_status(row, state.box),
            minutes=round(used.audio_seconds / SECONDS_PER_MINUTE, MINUTES_DECIMALS) if used else 0.0,
            calls=used.calls if used else 0))
    context = page_context(request, db, session)
    # Qui l'avviso del modello di testo compare anche con la coda vuota: è la pagina dove
    # lo si corregge.
    context["ai_notice"] = context["ai_notice"] or text_model_notice(db, state.box)
    return templates.TemplateResponse(request, "settings.html", context | {
        "crumbs": [("Impostazioni", None)], "providers": views, "box_available": state.box.available,
        "box_invalid": state.box.invalid,
        "paused": store.is_paused(db), "language": store.get_language(db), "languages": LANGUAGES,
        "counts": jobs.queue_counts(db), "backfill_count": jobs.count_backfill(db),
        "vocabulary": store.vocabulary(db), "retag_count": jobs.count_retag(db),
        "queued_now": queued,
        "vault_dir": str(state.settings.vault_dir), "vault_check": _vault_check(db),
        "ha": ha or ha_view(db, state.box)}, status_code=status_code)


@router.get("/settings")
def settings_page(request: Request, queued: int | None = None, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_login)):
    return render_settings(request, db, session, queued)


@router.post("/settings/general")
def save_general(language: str = Form(""), active: str | None = Form(None),
                 db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        store.set_language(db, language)
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    store.set_paused(db, active is None)
    db.commit()
    return _back()


@router.post("/settings/vocabulary")
def save_vocabulary(vocabulary: str = Form(""), db: Session = Depends(get_db),
                    session: WebSession = Depends(require_csrf)):
    store.set_vocabulary(db, vocabulary.splitlines())
    db.commit()
    return _back()


@router.post("/settings/retag")
def retag(request: Request, db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    n = jobs.enqueue_retag(db, request.app.state.clock())
    db.commit()
    return RedirectResponse(f"/settings?queued={n}", status_code=303)


@router.post("/settings/providers/{name}")
def save_provider(request: Request, name: str, api_key: str = Form(""),
                  transcribe_model: str = Form(""), text_model: str = Form(""),
                  enabled: str | None = Form(None), db: Session = Depends(get_db),
                  session: WebSession = Depends(require_csrf)):
    _spec(name)
    now = request.app.state.clock()
    store.ensure_providers(db, now)
    try:
        store.save_provider(db, request.app.state.box, name, api_key=api_key,
                            transcribe_model=transcribe_model, text_model=text_model,
                            enabled=enabled is not None, now=now)
    except store.NoSettingsKey:
        return PlainTextResponse(NO_SETTINGS_KEY, status_code=409)
    db.commit()
    return _back()


@router.post("/settings/providers/{name}/key/delete")
def delete_key(request: Request, name: str, db: Session = Depends(get_db),
               session: WebSession = Depends(require_csrf)):
    _spec(name)
    now = request.app.state.clock()
    store.ensure_providers(db, now)
    store.clear_api_key(db, name, now)
    db.commit()
    return _back()


@router.post("/settings/providers/{name}/move")
def move(request: Request, name: str, direction: str = Form(""), db: Session = Depends(get_db),
         session: WebSession = Depends(require_csrf)):
    _spec(name)
    if direction not in (store.MOVE_UP, store.MOVE_DOWN):
        return PlainTextResponse("direzione non valida", status_code=400)
    store.move_provider(db, name, direction, request.app.state.clock())
    db.commit()
    return _back()


@router.post("/settings/providers/{name}/test")
def probe_provider(request: Request, name: str, db: Session = Depends(get_db),
                   session: WebSession = Depends(require_csrf)) -> HTMLResponse:
    """"Prova": elenco dei modelli con la chiave salvata; mostra l'esito, mai la chiave."""
    _spec(name)
    box = request.app.state.box
    row = db.get(AiProvider, name)
    key = box.decrypt(row.api_key_enc) if row is not None and row.api_key_enc else None
    if not box.available:
        result = CheckResult(False, NO_SETTINGS_KEY)
    elif row is None or row.api_key_enc is None:
        result = CheckResult(False, "Nessuna chiave salvata")
    elif key is None:
        result = CheckResult(False, store.UNREADABLE_KEY)
    else:
        config = ProviderConfig(name=name, transcribe_model=row.transcribe_model,
                                text_model=row.text_model, api_key=key)
        result = build_provider(config, request.app.state.http).check()
    css = "ok" if result.ok else "ko"
    return HTMLResponse(f'<span class="test-result {css}" role="status">{escape(result.message)}</span>')


@router.post("/settings/backfill")
def backfill(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_csrf)):
    n = jobs.enqueue_backfill(db, request.app.state.clock())
    db.commit()
    return RedirectResponse(f"/settings?queued={n}", status_code=303)
