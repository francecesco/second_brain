import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from secondbrain import catalog
from secondbrain.models import Base
from tests.helpers import DEV, make_capture, make_device


def test_migrations_match_models(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_same_capture_id_can_appear_twice(db):
    db.add(make_device())
    db.flush()
    db.add(make_capture())
    db.add(make_capture(rel_path="2026/09/23/211530_70041dd8263c_2.wav", sha256="b" * 64))
    db.commit()
    found = catalog.find_captures(db, DEV, "cap_20260923_191530")
    assert [c.sha256 for c in found] == ["a" * 64, "b" * 64]
    assert catalog.find_captures(db, DEV, "cap_20260101_000000") == []


def test_rel_path_is_unique(db):
    db.add(make_device())
    db.flush()
    db.add(make_capture())
    db.add(make_capture())
    with pytest.raises(IntegrityError):
        db.commit()


def test_get_capture_and_device(db):
    cap = make_capture()
    db.add(make_device())
    db.flush()
    db.add(cap)
    db.commit()
    assert catalog.get_capture(db, cap.id).rel_path == cap.rel_path
    assert catalog.get_device(db, DEV).name == "e-paper"
    assert catalog.get_capture(db, uuid.uuid4()) is None
