"""Schermata "Oggi": bitmap per il device e anteprima PNG per le Impostazioni."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import digest, ha_store
from .ota import require_device
from .web.deps import require_login

router = APIRouter()


def _image(request: Request):
    """Immagine della schermata, o None se non c'è ancora una lettura da mostrare."""
    state = request.app.state
    with state.sessionmaker() as s:
        config = ha_store.get_config(s)
        snapshot = ha_store.get_snapshot(s)
    if config is None or snapshot.reading is None:
        return None
    return digest.render(snapshot, state.clock(), state.settings.tz_archive,
                         shopping=config.shopping_list is not None)


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
