from datetime import date, timedelta
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, text

from secondbrain import jobs, library
from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.models import Capture, Device, Job
from secondbrain.notefile import Note, render_note
from secondbrain.rescan import rescan
from secondbrain.sidecar import capture_to_sidecar
from tests.helpers import DEV, DEV2, NOW, TEST_DB, make_wav, upload


@pytest.fixture
def archive(lan_client, settings):
    upload(lan_client, make_wav(fill=b"\x01\x00"))
    upload(lan_client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260924_080000",
           ts="2026-09-24T08:00:00Z", device=DEV2)
    return Archive(settings.archive_dir)


def snapshot(db):
    db.expire_all()
    return {(c.rel_path, c.sha256, c.title, c.day, c.trashed_at, c.device_id)
            for c in db.scalars(select(Capture))}


def first(db):
    db.expire_all()
    return db.scalars(select(Capture).where(Capture.device_id == DEV)).one()


def test_rebuilds_an_empty_catalog(archive, db):
    before = snapshot(db)
    db.execute(delete(Capture))
    db.execute(delete(Device))
    db.commit()
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.added, report.updated, report.removed) == (2, 0, 0)
    assert sorted(report.devices_created) == [DEV, DEV2]
    assert snapshot(db) == before


def test_nothing_to_do_on_a_consistent_catalog(archive, db):
    report = rescan(db, archive, NOW)
    assert (report.added, report.updated, report.removed, report.problems) == (0, 0, 0, [])


def test_follows_files_moved_by_hand(archive, db, settings):
    cap = first(db)
    src, dst = settings.archive_dir / "2026/09/23", settings.archive_dir / "2026/09/20"
    dst.mkdir(parents=True)
    for p in list(src.iterdir()):
        p.rename(dst / p.name)
    assert rescan(db, archive, NOW).updated == 1
    db.commit()
    db.refresh(cap)
    assert cap.rel_path.startswith("2026/09/20/") and cap.day == date(2026, 9, 20)


def test_picks_up_title_edited_in_the_sidecar(archive, db):
    cap = first(db)
    data = archive.read_sidecar(cap.rel_path)
    data["title"] = "scritto a mano"
    archive.write_sidecar(cap.rel_path, data)
    assert rescan(db, archive, NOW).updated == 1
    db.commit()
    db.refresh(cap)
    assert cap.title == "scritto a mano"


def test_removes_rows_whose_files_are_gone(archive, db):
    cap = first(db)
    archive.delete(cap.rel_path)
    assert rescan(db, archive, NOW).removed == 1
    db.commit()
    assert db.get(Capture, cap.id) is None


def test_trash_location_marks_trashed(archive, db):
    cap = first(db)
    new = archive.move(cap.rel_path, ".trash/2026/09/23")
    rescan(db, archive, NOW)
    db.commit()
    db.refresh(cap)
    assert (cap.rel_path, cap.trashed_at, cap.day) == (new, NOW, date(2026, 9, 23))


def test_reports_problems_and_goes_on(archive, db, settings):
    (settings.archive_dir / "2026/09/23/235959_70041dd8263c.wav").write_bytes(make_wav())
    broken = settings.archive_dir / "2026/09/22"
    broken.mkdir(parents=True)
    (broken / "000000_70041dd8263c.wav").write_bytes(make_wav())
    (broken / "000000_70041dd8263c.json").write_text("{non json")
    report = rescan(db, archive, NOW)
    assert any("235959" in p and "senza sidecar" in p for p in report.problems)
    assert any("000000" in p and "non leggibile" in p for p in report.problems)
    assert (report.added, report.updated) == (0, 0)


def test_cli_rescan(archive, db, monkeypatch, capsys, settings):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    db.execute(delete(Capture))
    db.commit()
    assert main(["rescan"]) == 0
    out = capsys.readouterr().out
    assert "aggiunte 2" in out and "messe in coda da elaborare 2" in out


def test_idempotence_for_trashed_items(archive, db, settings):
    """Moving to trash by hand should be idempotent: second rescan has updated=0."""
    cap = first(db)
    # Move to trash manually (sidecar still has trashed_at: null)
    new_rel = archive.move(cap.rel_path, ".trash/2026/09/23")
    # First rescan: sets trashed_at to NOW, syncs sidecar
    report1 = rescan(db, archive, NOW)
    db.commit()
    db.refresh(cap)
    assert report1.updated == 1
    assert cap.trashed_at == NOW
    # Verify sidecar was synced
    sidecar = archive.read_sidecar(new_rel)
    assert sidecar["trashed_at"] == NOW.isoformat()
    # Second rescan: should be idempotent (updated=0)
    later = NOW + timedelta(days=1)
    report2 = rescan(db, archive, later)
    db.commit()
    db.refresh(cap)
    assert report2.updated == 0
    assert cap.trashed_at == NOW  # unchanged
    sidecar = archive.read_sidecar(new_rel)
    assert sidecar["trashed_at"] == NOW.isoformat()  # unchanged


def test_restores_sidecar_when_broken_but_wav_still_on_disk(archive, db):
    """Un sidecar illeggibile non deve far sparire la riga se il WAV c'è ancora: si
    ricostruisce il sidecar dal catalogo invece di buttare via titolo, data corretta,
    device e capture_id."""
    cap = first(db)
    archive.abs(cap.rel_path).with_suffix(".json").write_text("{non json")
    report = rescan(db, archive, NOW)
    assert report.removed == 0
    assert any("sidecar ricostruito dal catalogo" in p for p in report.problems)
    assert not any("senza sidecar" in p for p in report.problems)
    db.commit()
    db.refresh(cap)
    assert db.get(Capture, cap.id) is not None
    assert archive.read_sidecar(cap.rel_path) == capture_to_sidecar(cap)


def test_restores_sidecar_when_deleted_but_wav_still_on_disk(archive, db):
    """Stesso caso ma con il sidecar del tutto sparito (non solo corrotto)."""
    cap = first(db)
    archive.abs(cap.rel_path).with_suffix(".json").unlink()
    report = rescan(db, archive, NOW)
    assert report.removed == 0
    assert any("sidecar ricostruito dal catalogo" in p for p in report.problems)
    assert not any("senza sidecar" in p for p in report.problems)
    db.commit()
    db.refresh(cap)
    assert db.get(Capture, cap.id) is not None
    assert archive.read_sidecar(cap.rel_path) == capture_to_sidecar(cap)


def test_sidecar_id_mismatch_lets_sidecar_wins_over_old_row(archive, db):
    """Un sidecar valido ma con un id diverso da quello della riga che possiede oggi
    quel rel_path (es. modificato a mano) rivendica la posizione: vince il sidecar, la
    vecchia riga si toglie e il sidecar non va sovrascritto con i dati della vecchia
    riga (altrimenti si perderebbe il nuovo id, o peggio il rel_path finirebbe
    rivendicato due volte)."""
    cap = first(db)
    old_id = cap.id
    new_id = uuid.uuid4()
    data = archive.read_sidecar(cap.rel_path)
    data["id"] = str(new_id)
    archive.write_sidecar(cap.rel_path, data)
    report = rescan(db, archive, NOW)
    assert (report.removed, report.added) == (1, 1)
    db.commit()
    assert db.get(Capture, old_id) is None
    new_cap = db.get(Capture, new_id)
    assert new_cap is not None and new_cap.rel_path == cap.rel_path
    assert archive.read_sidecar(cap.rel_path)["id"] == str(new_id)


def test_restores_sidecar_when_broken_and_trashed(archive, db):
    """Combinazione non ancora coperta: riga nel cestino con sidecar corrotto. Deve
    restare nel cestino con il suo trashed_at, non tornare in archivio."""
    cap = first(db)
    new_rel = archive.move(cap.rel_path, ".trash/2026/09/23")
    rescan(db, archive, NOW)  # primo giro: marca trashed_at e sincronizza il sidecar
    db.commit()
    db.refresh(cap)
    archive.abs(new_rel).with_suffix(".json").write_text("{non json")
    report = rescan(db, archive, NOW + timedelta(days=1))
    assert report.removed == 0
    assert any("sidecar ricostruito dal catalogo" in p for p in report.problems)
    db.commit()
    db.refresh(cap)
    assert db.get(Capture, cap.id) is not None
    assert cap.trashed_at == NOW
    assert archive.read_sidecar(new_rel) == capture_to_sidecar(cap)


def test_sidecar_without_wav(archive, db, settings):
    """A .json sidecar without its .wav file is reported and not imported."""
    # Create a sidecar without its WAV
    orphan_dir = settings.archive_dir / "2026/09/22"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "120000_aabbccddeeff.json").write_text(
        '{"schema_version": 1, "id": "11111111-2222-3333-4444-555555555555", '
        '"device_id": "aabbccddeeff", "capture_id": "cap_20260922_120000", '
        '"recorded_at": "2026-09-22T12:00:00+00:00", "received_at": "2026-09-28T12:00:00+00:00", '
        '"duration_s": 1.0, "size_bytes": 32044, "sha256": "' + 'a' * 64 + '"}\n'
    )
    report = rescan(db, archive, NOW)
    assert any("120000_aabbccddeeff" in p and "sidecar senza WAV" in p for p in report.problems)
    assert report.added == 0  # not imported
    db.commit()
    assert db.get(Capture, uuid.UUID("11111111-2222-3333-4444-555555555555")) is None


ROME = ZoneInfo("Europe/Rome")
PROCESSED = Note(transcript="Devo chiamare Marco per il preventivo", title="Chiamare Marco",
                 summary="Preventivo del tetto.", tags=("lavoro",), language="it",
                 provider="groq", transcribe_model="whisper-large-v3-turbo",
                 enrich_provider="groq", enrich_model="openai/gpt-oss-120b", processed_at=NOW,
                 edited=("tags",))


def searchable(db, capture_id, words):
    return db.scalar(text("SELECT search_vector @@ websearch_to_tsquery('italian', :q) "
                          "FROM captures WHERE id = :id"), {"q": words, "id": capture_id})


def test_reads_ai_fields_from_the_note(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.updated, report.problems) == (1, [])
    cap = first(db)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.edited, cap.ai_provider,
            cap.processed_at) == (PROCESSED.transcript, "Chiamare Marco", "Preventivo del tetto.",
                                  ["lavoro"], ["tags"], "groq", NOW)
    assert searchable(db, cap.id, "chiamato")
    assert rescan(db, archive, NOW).updated == 0


def test_note_edited_by_hand_on_disk_is_picked_up(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    rescan(db, archive, NOW)
    db.commit()
    edited = archive.read_text(cap.rel_path, ".md").replace("title: Chiamare Marco",
                                                            "title: Telefonare a Marco")
    archive.write_text(cap.rel_path, ".md", edited)
    rescan(db, archive, NOW)
    db.commit()
    assert first(db).title_auto == "Telefonare a Marco"
    assert searchable(db, cap.id, "telefonare")


def test_rebuilt_catalog_keeps_the_ai_fields(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    db.execute(delete(Capture))
    db.commit()
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.added, report.queued) == (2, 1)  # solo quella senza .md torna in coda
    assert first(db).summary == "Preventivo del tetto."


def test_notes_without_md_are_queued_at_low_priority(archive, db):
    db.execute(delete(Job))
    db.commit()
    report = rescan(db, archive, NOW)
    db.commit()
    assert report.queued == 2
    assert {j.priority for j in db.scalars(select(Job))} == {jobs.PRIORITY_LOW}
    assert rescan(db, archive, NOW).queued == 0  # già in coda


def test_trashed_notes_are_not_queued(archive, db):
    library.trash_capture(db, archive, first(db).id, NOW)
    db.execute(delete(Job))
    db.commit()
    assert rescan(db, archive, NOW).queued == 1


def test_broken_note_is_reported_and_keeps_the_catalog(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    rescan(db, archive, NOW)
    jobs.mark_done(db, cap.id, NOW)
    db.commit()
    archive.write_text(cap.rel_path, ".md", "---\ntitle: [non chiusa\n---\n")
    report = rescan(db, archive, NOW)
    db.commit()
    assert any("nota .md non leggibile" in p for p in report.problems)
    assert (report.updated, report.queued) == (0, 0)
    assert first(db).summary == "Preventivo del tetto."


def test_deleted_note_clears_the_fields_and_requeues(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    rescan(db, archive, NOW)
    jobs.mark_done(db, cap.id, NOW)
    db.commit()
    archive.derived_path(cap.rel_path, ".md").unlink()
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.updated, report.queued) == (1, 1)
    assert (first(db).transcript, first(db).tags) == (None, [])


def test_note_running_right_now_is_left_alone(archive, db):
    """Il worker sta elaborando proprio ora la nota senza `.md`: rescan non la rimette in
    coda da capo (perderebbe i tentativi fatti e il lease del worker in corso)."""
    db.execute(delete(Job))
    db.commit()
    cap2 = db.scalars(select(Capture).where(Capture.device_id == DEV2)).one()
    jobs.enqueue(db, cap2.id, NOW, priority=jobs.PRIORITY_LOW)
    db.commit()
    running = jobs.claim(db, NOW)  # lo marca `running` con lease valido, e fa commit
    assert running.capture_id == cap2.id
    later = NOW + timedelta(minutes=1)
    report = rescan(db, archive, later)
    db.commit()
    assert report.queued == 1  # solo l'altra registrazione, senza lavoro
    job = jobs.get_job(db, cap2.id)
    assert (job.status, job.locked_until) == (jobs.RUNNING, running.locked_until)
