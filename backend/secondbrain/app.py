"""Applicazione FastAPI: stato condiviso, router, ciclo di vita (spec §5)."""
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import ingest, ota
from .archive import Archive
from .catalog import make_engine, make_sessionmaker
from .clock import utcnow
from .config import Settings, load_settings
from .httputil import public_host

log = logging.getLogger(__name__)

DEVICE_PATHS = ("/captures", "/firmware/")


def _is_device_path(path: str) -> bool:
    return path == DEVICE_PATHS[0] or path.startswith(DEVICE_PATHS[1])


def create_app(settings: Settings, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        removed = app.state.archive.clean_incoming()
        if removed:
            log.warning("rimossi %d upload incompleti da .incoming", removed)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="secondbrain", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.archive = Archive(settings.archive_dir)
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)

    if settings.device_hostname:
        @app.middleware("http")
        async def device_host_guard(request: Request, call_next):
            if (public_host(request) == settings.device_hostname
                    and not _is_device_path(request.url.path)):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return await call_next(request)

    app.include_router(ingest.router)
    app.include_router(ota.router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_settings())
