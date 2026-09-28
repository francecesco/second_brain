import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from secondbrain import library
from secondbrain.archive import Archive
from secondbrain.models import Capture
from tests.helpers import NOW, capture_by, make_wav, upload

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"
BASE = "211530_70041dd8263c"


@pytest.fixture
def arch(settings):
    return Archive(settings.archive_dir)


def test_set_title_updates_db_and_sidecar(recordings, db, arch):
    cap = library.set_title(db, arch, capture_by(db, CID).id, "  Idea per il digest  ")
    assert cap.title == "Idea per il digest"
    assert arch.read_sidecar(cap.rel_path)["title"] == "Idea per il digest"
    assert library.set_title(db, arch, cap.id, "   ").title is None


def test_correct_date_moves_derived_files(recordings, db, arch, settings):
    cap = capture_by(db, CID)
    arch.abs(cap.rel_path).with_suffix(".transcript.md").write_text("ciao")
    cap = library.correct_recorded_at(db, arch, ROME, cap.id, datetime(2026, 9, 20, 18, 5))
    assert cap.rel_path == f"2026/09/20/{BASE}.wav" and cap.day == date(2026, 9, 20)
    assert cap.recorded_at == datetime(2026, 9, 20, 16, 5, tzinfo=UTC)
    assert not cap.date_estimated
    assert arch.related_files(cap.rel_path) == [f"{BASE}.json", f"{BASE}.transcript.md", f"{BASE}.wav"]
    sidecar = arch.read_sidecar(cap.rel_path)
    assert sidecar["recorded_at"] == "2026-09-20T16:05:00+00:00" and sidecar["date_estimated"] is False
    assert not (settings.archive_dir / f"2026/09/23/{BASE}.wav").exists()


def test_correct_time_on_the_same_day_does_not_move(recordings, db, arch):
    cap = library.correct_recorded_at(db, arch, ROME, capture_by(db, CID).id, datetime(2026, 9, 23, 7, 0))
    assert cap.rel_path == f"2026/09/23/{BASE}.wav"


def test_correct_date_into_an_occupied_name(recordings, db, arch, settings):
    target = settings.archive_dir / "2026/09/20"
    target.mkdir(parents=True)
    (target / f"{BASE}.wav").write_bytes(b"altro")
    cap = library.correct_recorded_at(db, arch, ROME, capture_by(db, CID).id, datetime(2026, 9, 20, 12, 0))
    assert cap.rel_path == f"2026/09/20/{BASE}_2.wav"


def test_trash_restore_delete(recordings, db, arch, settings):
    cap = library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    assert cap.rel_path == f".trash/2026/09/23/{BASE}.wav" and cap.trashed_at == NOW
    assert arch.read_sidecar(cap.rel_path)["trashed_at"] == NOW.isoformat()
    cap = library.restore_capture(db, arch, cap.id)
    assert cap.rel_path == f"2026/09/23/{BASE}.wav" and cap.trashed_at is None
    with pytest.raises(library.NotInTrash):
        library.delete_capture(db, arch, cap.id)
    library.trash_capture(db, arch, cap.id, NOW)
    library.delete_capture(db, arch, cap.id)
    assert db.get(Capture, cap.id) is None
    assert not (settings.archive_dir / ".trash/2026").exists()


def test_restore_into_an_occupied_name(recordings, db, arch, settings):
    cap = library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    (settings.archive_dir / f"2026/09/23/{BASE}.wav").write_bytes(b"altro")
    assert library.restore_capture(db, arch, cap.id).rel_path == f"2026/09/23/{BASE}_2.wav"


def test_correct_date_refused_in_trash(recordings, db, arch):
    cap = library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    with pytest.raises(library.InTrash):
        library.correct_recorded_at(db, arch, ROME, cap.id, datetime(2026, 9, 20, 12, 0))


def test_purge_respects_retention(recordings, db, arch):
    old = library.trash_capture(db, arch, capture_by(db, CID).id, NOW - timedelta(days=31))
    recent = library.trash_capture(db, arch, capture_by(db, "cap_20260923_080000").id,
                                   NOW - timedelta(days=29))
    assert library.purge_trash(db, arch, NOW, 30) == 1
    assert db.get(Capture, old.id) is None and db.get(Capture, recent.id) is not None


def test_unknown_capture(db, arch):
    with pytest.raises(library.NotFound):
        library.set_title(db, arch, uuid.uuid4(), "x")


def test_device_retry_after_trash_is_still_a_duplicate(recordings, db, arch):
    library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    assert upload(recordings.client, make_wav(fill=b"\x01\x00")).status_code == 409
