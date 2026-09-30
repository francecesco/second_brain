"""Contesto comune delle pagine del finder: filtro dispositivo, fuso, albero."""
from sqlalchemy.orm import Session
from starlette.requests import Request

from .. import catalog, jobs
from .. import settings_store as store
from ..models import WebSession
from ..naming import is_valid_device_id

LOCAL_FORMAT = "%d/%m/%Y %H:%M:%S"
NOTICE_NO_KEY = "Elaborazione AI ferma: manca SETTINGS_KEY nel .env."
NOTICE_PAUSED = "Elaborazione AI in pausa: le note nuove restano in coda."
NOTICE_NO_PROVIDER = "Nessun provider AI con una chiave: le note restano in coda."


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
    }


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
        return NOTICE_NO_KEY
    if store.is_paused(db):
        return NOTICE_PAUSED
    if not store.provider_configs(db, box):
        return NOTICE_NO_PROVIDER
    return None


def page_context(request: Request, db: Session, session: WebSession, device: str | None = None,
                 year: int | None = None, month: int | None = None) -> dict:
    return base_context(request, db, session, device) | {
        "ai_notice": ai_notice(db, request.app.state.box),
        "year": year,
        "month": month,
        "tree": {
            "years": catalog.year_counts(db, device),
            "months": catalog.month_counts(db, year, device) if year else [],
            "days": catalog.day_counts(db, year, month, device) if year and month else [],
        },
    }
