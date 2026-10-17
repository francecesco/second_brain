"""Contesto comune delle pagine del finder: filtro dispositivo, fuso, albero."""
from sqlalchemy.orm import Session
from starlette.requests import Request

from .. import catalog, jobs
from .. import settings_store as store
from ..models import WebSession
from ..naming import is_valid_device_id

LOCAL_FORMAT = "%d/%m/%Y %H:%M:%S"
NOTICE_NO_KEY = "Elaborazione AI ferma: manca SETTINGS_KEY nel .env."
NOTICE_BAD_KEY = "Elaborazione AI ferma: SETTINGS_KEY non valida nel .env."
NOTICE_PAUSED = "Elaborazione AI in pausa: le note nuove restano in coda."
NOTICE_NO_PROVIDER = "Nessun provider AI con una chiave: le note restano in coda."
NOTICE_NO_TEXT_MODEL = ("Nessun provider AI ha un modello di testo: titolo, riassunto e tag "
                        "non si generano.")


def clean_device(value: str | None) -> str | None:
    return value if value and is_valid_device_id(value) else None


def base_context(request: Request, db: Session, session: WebSession,
                 device: str | None = None) -> dict:
    tz = request.app.state.settings.tz_archive
    devices = catalog.list_devices(db)
    return {
        "csrf": session.csrf_token,
        "tz": tz,
        "local": lambda dt: dt.astimezone(tz).strftime(LOCAL_FORMAT),
        "device": device,
        "devices": devices,
        "device_names": {d.id: d.name for d in devices},
        "qs": f"?device={device}" if device else "",
        "estimated_count": catalog.count_estimated(db),
        "todo_count": catalog.count_todo(db, device),
        "done_count": catalog.count_done(db, device),
    }


def text_model_notice(db: Session, box: store.SecretBox) -> str | None:
    """Ci sono provider utilizzabili, ma nessuno con un modello di testo (es. solo OpenAI,
    che non ne ha uno di default): le note si trascrivono e non si arricchiscono mai."""
    configs = store.provider_configs(db, box) if box.available else []
    if configs and not any(config.text_model for config in configs):
        return NOTICE_NO_TEXT_MODEL
    return None


def ai_notice(db: Session, box: store.SecretBox) -> str | None:
    """Perché le note in coda non avanzano, se c'è un motivo (spec AI §11).

    Ruling P3: l'ultimo controllo passa da `provider_configs`, che decifra ogni chiave
    con `box` e scarta provider disabilitati o con una chiave non più leggibile: una
    chiave salvata ma cifrata con un'altra SETTINGS_KEY deve comunque far comparire
    l'avviso, non solo l'assenza di una chiave salvata.
    """
    if not jobs.queue_counts(db)[jobs.QUEUED]:
        return None
    if not box.available:
        return NOTICE_BAD_KEY if box.invalid else NOTICE_NO_KEY
    if store.is_paused(db):
        return NOTICE_PAUSED
    if not store.provider_configs(db, box):
        return NOTICE_NO_PROVIDER
    return text_model_notice(db, box)


def today_local(request: Request):
    """Il giorno di oggi nel fuso dell'archivio (gruppi "Oggi"/"Ieri", tessere)."""
    return request.app.state.clock().astimezone(request.app.state.settings.tz_archive).date()


def page_context(request: Request, db: Session, session: WebSession, device: str | None = None,
                 year: int | None = None, month: int | None = None) -> dict:
    years = catalog.year_counts(db, device)
    # L'albero tiene aperto l'anno della pagina o, fuori dall'archivio, il più recente
    # (spec UI responsive §2.5): i mesi sono sempre a un clic.
    tree_year = year or (years[0][0] if years else None)
    months = catalog.month_counts(db, tree_year, device) if tree_year else []
    return base_context(request, db, session, device) | {
        "ai_notice": ai_notice(db, request.app.state.box),
        "year": year,
        "month": month,
        "tree": {
            "years": years,
            "year": tree_year,
            "months": months,
            "month_max": max((n for _, n in months), default=0),
            "days": catalog.day_counts(db, year, month, device) if year and month else [],
        },
    }
