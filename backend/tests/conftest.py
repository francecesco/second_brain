"""Fixture condivise: Postgres di test migrato, sessione su DB svuotato."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from secondbrain.catalog import make_engine, make_sessionmaker
from secondbrain.models import Base
from tests.helpers import TEST_DB

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
