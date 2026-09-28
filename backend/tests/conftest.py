"""Fixture condivise: Postgres di test migrato, sessione su DB svuotato."""
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from secondbrain.app import create_app
from secondbrain.catalog import make_engine, make_sessionmaker
from secondbrain.config import Settings
from secondbrain.devices import create_device
from secondbrain.models import Base
from tests.helpers import DEV, LAN_CLIENT, NOW, TEST_DB

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001 - qualunque errore di connessione
        pytest.exit(
            f"Postgres di test non raggiungibile su {TEST_DB}.\n"
            f"Avvialo con: docker compose -f docker-compose.test.yml up -d\n({exc})",
            returncode=2,
        )
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """Sessione su un DB svuotato prima del test."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with make_sessionmaker(engine)() as session:
        yield session


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=TEST_DB, archive_dir=tmp_path / "archive",
                    firmware_dir=tmp_path / "firmware", tz_archive=ZoneInfo("Europe/Rome"))


@pytest.fixture
def make_client(db, clock):
    opened = []

    def factory(settings, client_addr=LAN_CLIENT):
        client = TestClient(create_app(settings, clock=clock), client=client_addr)
        client.__enter__()  # esegue il lifespan
        opened.append(client)
        return client

    yield factory
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client, settings):
    return make_client(settings)


@pytest.fixture
def lan_client(make_client, settings):
    return make_client(replace(settings, allow_unauthenticated_lan=True))


@pytest.fixture
def token(db):
    _, tok = create_device(db, DEV, "e-paper", "epaper154", NOW)
    db.commit()
    return tok
