"""Applicazione FastAPI: stato condiviso, router, ciclo di vita (spec §5)."""
import asyncio
import logging
import mimetypes
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ingest, library, ota
from .archive import Archive
from .catalog import make_engine, make_sessionmaker
from .clock import utcnow
from .config import BAD_SETTINGS_KEY, Settings, load_settings
from .httputil import public_host
from .settings_store import SecretBox
from .web import actions as web_actions
from .web import ai as web_ai
from .web import browse as web_browse
from .web import login as web_login
from .web import search as web_search
from .web import settings as web_settings
from .web.auth import CsrfError, NotAuthenticated
from .web.templating import STATIC_DIR

log = logging.getLogger(__name__)

DEVICE_PATHS = ("/captures", "/firmware/")
PURGE_INTERVAL_S = 24 * 3600


def _purge_once(app: FastAPI) -> int:
    with app.state.sessionmaker() as s:
        return library.purge_trash(s, app.state.archive, app.state.clock(),
                                   app.state.settings.trash_retention_days)


async def _purge(app: FastAPI) -> None:
    try:
        removed = await run_in_threadpool(_purge_once, app)
    except Exception:  # noqa: BLE001 - la pulizia non deve fermare il servizio
        log.exception("pulizia del cestino fallita")
        return
    if removed:
        log.info("cestino: eliminate %d registrazioni scadute", removed)


async def _purge_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(PURGE_INTERVAL_S)
        await _purge(app)


def _is_device_path(path: str) -> bool:
    return path == DEVICE_PATHS[0] or path.startswith(DEVICE_PATHS[1])


def create_app(settings: Settings, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        removed = app.state.archive.clean_incoming()
        if removed:
            log.warning("rimossi %d upload incompleti da .incoming", removed)
        await _purge(app)
        task = asyncio.create_task(_purge_loop(app))
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.http.close()
        app.state.engine.dispose()

    app = FastAPI(title="secondbrain", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.archive = Archive(settings.archive_dir)
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.box = SecretBox(settings.settings_key, invalid=settings.settings_key_invalid)
    if settings.settings_key_invalid:
        log.error(BAD_SETTINGS_KEY)  # mai il valore: potrebbe essere una chiave quasi giusta
    app.state.http = httpx.Client()  # solo per "Prova"; nei test lo si sostituisce con un MockTransport

    if settings.device_hostname:
        @app.middleware("http")
        async def device_host_guard(request: Request, call_next):
            if (public_host(request) == settings.device_hostname
                    and not _is_device_path(request.url.path)):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return await call_next(request)

    app.include_router(ingest.router)
    app.include_router(ota.router)

    mimetypes.add_type("font/woff2", ".woff2")  # l'immagine slim non lo conosce: senza, il font esce come octet-stream
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(web_login.router)
    app.include_router(web_browse.router)
    app.include_router(web_actions.router)
    app.include_router(web_ai.router)
    app.include_router(web_search.router)
    app.include_router(web_settings.router)

    @app.exception_handler(NotAuthenticated)
    async def login_required(request: Request, exc: NotAuthenticated) -> Response:
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, exc: CsrfError) -> Response:
        return PlainTextResponse("token CSRF mancante o non valido", status_code=403)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_settings())
