import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from secondbrain import jobs, library
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.models import Capture
from secondbrain.notefile import Note, parse_note
from secondbrain.notes import lock_capture, note_from_capture, write_note
from tests.helpers import NOW, capture_by, is_searchable

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"
NOTE = Note(transcript="Devo chiamare Marco per il preventivo", title="Chiamare Marco",
            summary="Preventivo del tetto.", tags=("lavoro",), language="it", provider="groq",
            transcribe_model="whisper-large-v3-turbo", enrich_provider="groq",
            enrich_model="openai/gpt-oss-120b", processed_at=NOW)


@pytest.fixture
def arch(settings):
    return Archive(settings.archive_dir)


def fresh(db, capture_id) -> Capture:
    db.expire_all()
    return db.get(Capture, capture_id)


def test_write_note_updates_disk_catalog_and_search(recordings, db, arch):
    cap = capture_by(db, CID)
    write_note(db, arch, cap, NOTE, ROME)
    db.commit()
    assert parse_note(arch.read_text(cap.rel_path, ".md")) == NOTE
    cap = fresh(db, cap.id)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.ai_provider) == (
        NOTE.transcript, "Chiamare Marco", "Preventivo del tetto.", ["lavoro"], "groq")
    assert note_from_capture(cap) == NOTE
    assert is_searchable(db, cap.id, "chiamato") and is_searchable(db, cap.id, "lavoro")
    assert not is_searchable(db, cap.id, "spesa")


def test_manual_title_is_searchable(recordings, db, arch):
    cap = library.set_title(db, arch, capture_by(db, CID).id, "Idea per il digest")
    assert is_searchable(db, cap.id, "digest")


def test_edit_summary_marks_it_edited(recordings, db, arch):
    cap = capture_by(db, CID)
    write_note(db, arch, cap, NOTE, ROME)
    db.commit()
    library.edit_ai_field(db, arch, ROME, cap.id, "summary", "  Mio   riassunto ")
    cap = fresh(db, cap.id)
    assert (cap.summary, cap.edited, cap.title_auto) == ("Mio riassunto", ["summary"], "Chiamare Marco")
    on_disk = parse_note(arch.read_text(cap.rel_path, ".md"))
    assert (on_disk.summary, on_disk.edited) == ("Mio riassunto", ("summary",))
    assert is_searchable(db, cap.id, "riassunto")


def test_edit_tags_and_transcript(recordings, db, arch):
    cap_id = capture_by(db, CID).id
    library.edit_ai_field(db, arch, ROME, cap_id, "transcript", " Testo corretto a mano ")
    library.edit_ai_field(db, arch, ROME, cap_id, "tags", "Lavoro, #casa")
    cap = fresh(db, cap_id)
    assert (cap.tags, cap.transcript, cap.edited) == (
        ["lavoro", "casa"], "Testo corretto a mano", ["transcript", "tags"])


def test_edit_summary_or_tags_refused_before_processing(recordings, db, arch):
    cap_id = capture_by(db, CID).id
    jobs.drop(db, cap_id)  # come una nota il cui lavoro è finito o è stato cancellato
    before = jobs.count_backfill(db)
    for field in ("summary", "tags"):
        with pytest.raises(library.NotProcessed):
            library.edit_ai_field(db, arch, ROME, cap_id, field, "x")
    cap = fresh(db, cap_id)
    assert cap.transcript is None
    with pytest.raises(FileNotFoundError):
        arch.read_text(cap.rel_path, ".md")
    assert jobs.count_backfill(db) == before


def test_edit_refused_in_trash_unknown_field_or_capture(recordings, db, arch):
    cap_id = capture_by(db, CID).id
    with pytest.raises(ValueError):
        library.edit_ai_field(db, arch, ROME, cap_id, "title", "x")
    with pytest.raises(library.NotFound):
        library.edit_ai_field(db, arch, ROME, uuid.uuid4(), "summary", "x")
    library.trash_capture(db, arch, cap_id, NOW)
    with pytest.raises(library.InTrash):
        library.edit_ai_field(db, arch, ROME, cap_id, "summary", "x")


def test_edits_wait_for_the_row_lock_held_by_the_worker(recordings, db, engine, arch):
    cap_id = capture_by(db, CID).id
    db.commit()
    worker = make_sessionmaker(engine)()
    try:
        assert lock_capture(worker, cap_id) is not None  # il worker sta scrivendo la nota
        for action in (lambda: library.edit_ai_field(db, arch, ROME, cap_id, "summary", "x"),
                       lambda: library.set_title(db, arch, cap_id, "x")):
            db.execute(text("SET lock_timeout = '200ms'"))
            with pytest.raises(OperationalError):
                action()
            db.rollback()
    finally:
        worker.rollback()
        worker.close()
    assert fresh(db, cap_id).summary is None
