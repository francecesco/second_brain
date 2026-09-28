"""Contesto comune delle pagine del finder: filtro dispositivo, fuso, albero."""
from sqlalchemy.orm import Session
from starlette.requests import Request

from .. import catalog
from ..models import WebSession
from ..naming import is_valid_device_id

LOCAL_FORMAT = "%d/%m/%Y %H:%M:%S"


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


def page_context(request: Request, db: Session, session: WebSession, device: str | None = None,
                 year: int | None = None, month: int | None = None) -> dict:
    return base_context(request, db, session, device) | {
        "year": year,
        "month": month,
        "tree": {
            "years": catalog.year_counts(db, device),
            "months": catalog.month_counts(db, year, device) if year else [],
            "days": catalog.day_counts(db, year, month, device) if year and month else [],
        },
    }
