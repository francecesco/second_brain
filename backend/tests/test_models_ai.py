from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text

from secondbrain.models import Capture, Job
from tests.helpers import NOW, TEST_DB, make_capture, make_device

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_new_capture_columns_have_defaults(db):
    db.add(make_device())
    db.flush()  # niente relazione Device-Capture: l'ordine degli INSERT lo decide il flush
    cap = make_capture()
    db.add(cap)
    db.commit()
    db.expire_all()
    c = db.get(Capture, cap.id)
    assert (c.tags, c.edited, c.transcript, c.title_auto, c.summary, c.job) == ([], [], None, None, None, None)


def test_job_goes_away_with_its_capture(db):
    db.add(make_device())
    db.flush()  # niente relazione Device-Capture: l'ordine degli INSERT lo decide il flush
    cap = make_capture()
    db.add(cap)
    db.flush()
    db.add(Job(capture_id=cap.id, status="queued", stage="transcribe", priority=100, attempts=0,
               next_run_at=NOW, locked_until=None, last_error=None, updated_at=NOW))
    db.commit()
    db.expire_all()
    assert db.get(Capture, cap.id).job.status == "queued"
    db.delete(db.get(Capture, cap.id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_search_and_tag_indexes_are_gin(db):
    rows = dict(db.execute(text(
        "select indexname, indexdef from pg_indexes where tablename = 'captures'")).all())
    assert "USING gin" in rows["ix_captures_search_vector"]
    assert "USING gin" in rows["ix_captures_tags"]


def test_migration_0004_downgrades_and_upgrades(engine):
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "0003")
    tables = set(inspect(engine).get_table_names())
    assert not tables & {"jobs", "settings", "ai_providers", "ai_usage"}
    command.upgrade(cfg, "head")
    assert {"jobs", "settings", "ai_providers", "ai_usage"} <= set(inspect(engine).get_table_names())
