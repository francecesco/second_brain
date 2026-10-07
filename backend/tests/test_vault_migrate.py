from zoneinfo import ZoneInfo

from secondbrain import vault_migrate
from secondbrain.archive import Archive
from secondbrain.notefile import Note, render_note
from secondbrain.vault import Vault
from tests.helpers import capture_by

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"


def test_migrate_moves_archive_notes_into_the_vault_once(recordings, db, settings):
    archive, vault = Archive(settings.archive_dir), Vault(settings.vault_dir)
    capture = capture_by(db, CID)
    archive.write_text(capture.rel_path, ".md", render_note(Note(transcript="Vecchia.", title="Vecchia nota"), ROME))
    assert vault_migrate.migrate(db, archive, vault, ROME, None) == 1
    db.expire_all()
    c = capture_by(db, CID)
    assert c.note_path == "Captures/2026/2026-09-23 21.15 Vecchia nota.md" and c.transcript == "Vecchia."
    assert "id: cap_20260923_191530" in vault.read_text(c.note_path)
    assert not archive.derived_path(c.rel_path, ".md").exists()
    assert vault_migrate.migrate(db, archive, vault, ROME, None) == 0


def test_migrate_skips_unreadable_notes_and_reports(recordings, db, settings):
    archive, vault = Archive(settings.archive_dir), Vault(settings.vault_dir)
    capture = capture_by(db, CID)
    archive.write_text(capture.rel_path, ".md", "---\ntitle: [\n---\n")
    assert vault_migrate.migrate(db, archive, vault, ROME, None) == 0
    assert archive.derived_path(capture.rel_path, ".md").exists()   # lasciata dov'e'
