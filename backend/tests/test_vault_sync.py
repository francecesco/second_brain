import os

from secondbrain import jobs, notefile, notes, vault_sync
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.vault import Vault
from tests.ai_fakes import processed
from tests.helpers import NOW, capture_by

CID = "cap_20260923_191530"


def touch(vault, rel, old, new):
    """Modifica fatta in Obsidian: Syncthing conserva l'mtime del mittente, basta che sia diverso."""
    path = vault.abs(rel)
    path.write_text(path.read_text().replace(old, new))
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))


def check(db, settings):
    return vault_sync.check(make_sessionmaker(db.get_bind()), Vault(settings.vault_dir),
                            settings.tz_archive, None, NOW, archive=Archive(settings.archive_dir))


def test_changed_body_and_tags_become_manual_edits(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto", transcript="Vecchio testo.", tags=("casa",))
    vault = Vault(settings.vault_dir)
    rel = capture_by(db, CID).note_path
    touch(vault, rel, "Vecchio testo.", "Nuovo testo.")
    touch(vault, rel, "tags: [casa]", "tags: [casa, giardino]")
    report = check(db, settings)
    db.expire_all()
    c = capture_by(db, CID)
    assert report.changed == 1 and report.problems == []
    assert c.transcript == "Nuovo testo." and c.tags == ["casa", "giardino"]
    assert set(c.edited) == {"transcript", "tags"} and c.note_mtime == vault.mtime(rel)
    assert "Nuovo testo." in vault.read_text(rel)            # riscritta normalizzata
    assert check(db, settings).changed == 0                 # idempotente


def test_changed_title_becomes_the_manual_title_and_renames(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    rel = capture_by(db, CID).note_path
    touch(vault, rel, "title: Auto", "title: Titolo mio")
    check(db, settings)
    db.expire_all()
    c = capture_by(db, CID)
    assert c.title == "Titolo mio" and c.title_auto == "Auto"
    assert c.note_path == "Captures/2026/2026-09-23 21.15 Titolo mio.md" and vault.mtime(rel) is None


def test_renamed_file_sets_the_manual_title(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    vault.rename(capture_by(db, CID).note_path, "Captures/2026/2026-09-23 21.15 Rinominata a mano.md")
    report = check(db, settings)
    db.expire_all()
    c = capture_by(db, CID)
    assert report.changed == 1 and c.title == "Rinominata a mano"
    assert c.note_path == "Captures/2026/2026-09-23 21.15 Rinominata a mano.md"


def test_unknown_id_missing_id_broken_and_conflicts_are_reported_not_applied(recordings, db, settings):
    vault = Vault(settings.vault_dir)
    vault.write_text("Captures/2026/2026-01-01 10.00 Estranea.md", "---\nid: cap_20260101_100000\n---\nx\n")
    vault.write_text("Captures/2026/2026-01-01 10.00 Senza id.md", "---\ntitle: x\n---\nx\n")
    vault.write_text("Captures/2026/2026-01-01 10.00 Rotta.md", "---\nid: [\n---\n")
    vault.write_text("Captures/2026/2026-01-01 10.00 Rotta.sync-conflict-20260101-100000-ABCDEF.md", "x")
    report = check(db, settings)
    assert report.changed == 0 and len(report.problems) == 4
    assert check(db, settings).problems == report.problems   # stessi problemi finche' i file non cambiano


def test_edit_from_obsidian_survives_a_running_transcription(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto", transcript="Dal provider.")
    jobs.requeue(db, capture_by(db, CID), NOW)
    db.commit()
    running = jobs.claim(db, NOW)
    while running is not None and running.capture_id != cid:   # le altre registrazioni di prova sono in coda prima
        running = jobs.claim(db, NOW)
    db.commit()
    assert running is not None and running.capture_id == cid
    vault = Vault(settings.vault_dir)
    touch(vault, capture_by(db, CID).note_path, "Dal provider.", "Corretto in Obsidian.")
    check(db, settings)
    # Il worker scrive dopo: rilegge `edited` sotto il lock e lascia la trascrizione corretta.
    capture = notes.lock_capture(db, cid)
    note = notefile.apply_transcript(notes.note_from_capture(capture), "Di nuovo dal provider.",
                                     provider="groq", model="m", language="it", at=NOW)
    notes.write_note(db, Archive(settings.archive_dir), capture, note, settings.tz_archive,
                     vault=vault)
    db.commit()
    db.expire_all()
    c = capture_by(db, CID)
    assert c.transcript == "Corretto in Obsidian." and "Corretto in Obsidian." in vault.read_text(c.note_path)


def test_title_from_file_name_with_and_without_prefix(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    vault.rename(capture_by(db, CID).note_path, "Captures/2026/Spesa settimanale al mercato.md")
    check(db, settings)
    db.expire_all()
    c = capture_by(db, CID)
    assert c.title == "Spesa settimanale al mercato"
    assert c.note_path == "Captures/2026/2026-09-23 21.15 Spesa settimanale al mercato.md"   # nome normalizzato


def test_title_from_obsidian_survives_rescan_and_keeps_the_auto_title(recordings, db, settings):
    from secondbrain.rescan import rescan
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    rel = capture_by(db, CID).note_path
    touch(vault, rel, "title: Auto", "title: Da Obsidian")
    check(db, settings)
    rescan(db, Archive(settings.archive_dir), NOW, vault=vault)
    db.commit()
    db.expire_all()
    c = capture_by(db, CID)
    assert (c.title, c.title_auto) == ("Da Obsidian", "Auto")
    assert Archive(settings.archive_dir).read_sidecar(c.rel_path)["title"] == "Da Obsidian"


def test_one_bad_file_does_not_stop_the_others(recordings, db, settings, monkeypatch):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto", transcript="Vecchio.")
    vault = Vault(settings.vault_dir)
    rel = capture_by(db, CID).note_path
    other = capture_by(db, "cap_20260923_080000")
    processed(db, settings, other.id, title="Prima")
    first_rel = capture_by(db, "cap_20260923_080000").note_path   # ordinato prima di rel (08.00 < 21.15)
    touch(vault, first_rel, "title: Prima", "title: Esplosiva")
    touch(vault, rel, "Vecchio.", "Nuovo.")
    real_write = vault_sync.write_note

    def exploding_write(s, archive, capture, note, tz, **kw):
        if capture.note_path == first_rel:
            raise RuntimeError("bug simulato su questa nota")
        return real_write(s, archive, capture, note, tz, **kw)

    monkeypatch.setattr(vault_sync, "write_note", exploding_write)
    report = check(db, settings)
    db.expire_all()
    assert capture_by(db, CID).transcript == "Nuovo." and report.changed == 1
    assert any("bug simulato" in p for p in report.problems)


def test_file_is_reread_under_the_lock(recordings, db, settings, monkeypatch):
    # Se il file cambia tra la prima lettura e il lock, si rilegge: nessun campo torna indietro.
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto", transcript="Vecchio.")
    vault = Vault(settings.vault_dir)
    rel = capture_by(db, CID).note_path
    touch(vault, rel, "Vecchio.", "Prima lettura.")
    real_lock = vault_sync.lock_capture

    def lock_then_edit(s, capture_id):
        touch(vault, rel, "Prima lettura.", "Seconda lettura.")
        return real_lock(s, capture_id)

    monkeypatch.setattr(vault_sync, "lock_capture", lock_then_edit)
    check(db, settings)
    db.expire_all()
    assert capture_by(db, CID).transcript == "Seconda lettura."
