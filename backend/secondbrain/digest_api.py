"""Schermata "Oggi": bitmap per il device e anteprima PNG per le Impostazioni."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from datetime import date

from . import catalog, digest, ha_store
from .ota import require_device
from .web.deps import require_login

router = APIRouter()


def _image(request: Request):
    """Immagine della schermata, o None se non c'è niente da mostrare: né una lettura di
    Home Assistant né azioni scadute o di oggi (spec azioni §6)."""
    state = request.app.state
    with state.sessionmaker() as s:
        config = ha_store.get_config(s)
        snapshot = ha_store.get_snapshot(s)
        actions = [(a["text"], date.fromisoformat(a["due"]))
                   for _, a in catalog.open_actions(s) if a.get("due")]
    now, tz = state.clock(), state.settings.tz_archive
    if config is None or snapshot.reading is None:
        if not digest.action_rows(actions, now.astimezone(tz).date()):
            return None
        snapshot = None
    return digest.render(snapshot, now, tz, actions)


def _matches(header: str | None, tag: str) -> bool:
    if not header:
        return False
    return any(p.strip().removeprefix("W/") == tag for p in header.split(","))


@router.get("/digest/today", dependencies=[Depends(require_device)])
def today(request: Request) -> Response:
    image = _image(request)
    if image is None:
        return Response(status_code=204)
    data = digest.pack(image)
    tag = digest.etag(data)
    headers = {"ETag": tag, "Cache-Control": "no-store"}
    if _matches(request.headers.get("if-none-match"), tag):
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="application/octet-stream", headers=headers)


@router.get("/settings/homeassistant/preview.png", dependencies=[Depends(require_login)])
def preview(request: Request) -> Response:
    image = _image(request)
    if image is None:
        raise HTTPException(404, "niente da mostrare")
    return Response(digest.preview_png(image), media_type="image/png",
                    headers={"Cache-Control": "no-store"})
