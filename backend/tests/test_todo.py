"""Note da fare (spec note da fare): spunta, vista "Da fare", stato nel sidecar e nel vault."""
from zoneinfo import ZoneInfo

from secondbrain import catalog, library
from secondbrain.archive import Archive
from secondbrain.rescan import rescan
from secondbrain.vault import Vault
from tests.ai_fakes import processed
from tests.helpers import DEV, DEV2, NOW, capture_by, htmx

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"


def test_set_done_updates_db_and_sidecar(recordings, db, settings):
    arch = Archive(settings.archive_dir)
    cap = library.set_done(db, arch, capture_by(db, CID).id, True, NOW)
    assert cap.done_at == NOW
    assert arch.read_sidecar(cap.rel_path)["done_at"] == NOW.isoformat()
    cap = library.set_done(db, arch, cap.id, False, NOW)
    assert cap.done_at is None and arch.read_sidecar(cap.rel_path)["done_at"] is None


def test_done_is_written_in_the_vault_note(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    library.set_done(db, Archive(settings.archive_dir), cid, True, NOW, vault=vault, tz=ROME)
    text = vault.read_text(capture_by(db, CID).note_path)
    assert "completata: true\n" in text
    library.set_done(db, Archive(settings.archive_dir), cid, False, NOW, vault=vault, tz=ROME)
    assert "completata: false\n" in vault.read_text(capture_by(db, CID).note_path)


def test_todo_list_and_count_follow_done_trash_and_device(recordings, db, settings):
    arch = Archive(settings.archive_dir)
    assert catalog.count_todo(db) == 3
    assert [c.capture_id for c in catalog.list_todo(db)] == [
        CID, "cap_20260923_080000", "cap_20260810_070000"]          # dalla piu' recente
    assert catalog.count_todo(db, DEV2) == 1
    library.set_done(db, arch, capture_by(db, CID).id, True, NOW)
    library.trash_capture(db, arch, capture_by(db, "cap_20260810_070000").id, NOW)
    db.commit()
    assert [c.capture_id for c in catalog.list_todo(db)] == ["cap_20260923_080000"]
    assert catalog.count_todo(db, DEV) == 0


def test_rescan_keeps_done(recordings, db, settings):
    arch = Archive(settings.archive_dir)
    cid = capture_by(db, CID).id
    library.set_done(db, arch, cid, True, NOW)
    db.commit()
    cap = capture_by(db, CID)
    cap.done_at = None          # catalogo disallineato: vince il sidecar
    db.commit()
    rescan(db, arch, NOW)
    db.commit()
    assert capture_by(db, CID).done_at == NOW


def test_checking_removes_the_row_and_updates_counters(recordings, db):
    # In ogni lista la riga sparisce: restano gli aggiornamenti out-of-band di menu e albero.
    cap = capture_by(db, CID)
    c = recordings.client
    assert c.post(f"/captures/{cap.id}/done").status_code == 403          # CSRF
    headers = htmx(recordings) | {"HX-Current-URL": "http://test/browse/2026/09/23"}
    r = c.post(f"/captures/{cap.id}/done", headers=headers)
    assert r.status_code == 200 and f'id="row-{cap.id}"' not in r.text
    assert 'id="nav-todo"' in r.text and "Da fare (2)" in r.text
    assert 'id="nav-done"' in r.text and "Completati (1)" in r.text
    assert 'id="tree"' in r.text and r.text.count('hx-swap-oob="outerHTML"') == 3
    assert c.post("/captures/00000000-0000-0000-0000-000000000000/done",
                  headers=htmx(recordings)).status_code == 404


def test_counters_follow_the_device_of_the_current_page(recordings, db):
    cap = capture_by(db, CID)
    headers = htmx(recordings) | {"HX-Current-URL": f"http://test/todo?device={DEV}"}
    r = recordings.client.post(f"/captures/{cap.id}/done", headers=headers)
    assert 'id="nav-todo" class="on" href="/todo?device=' in r.text and "Da fare (1)" in r.text


def test_done_notes_leave_archive_and_todo_and_go_to_completed(recordings, db):
    c = recordings.client
    assert "Da fare (3)" in c.get("/todo").text
    cap = capture_by(db, CID)
    c.post(f"/captures/{cap.id}/done", headers=htmx(recordings))
    assert "21:15:30" not in c.get("/browse/2026/09/23").text            # sparita dal giorno
    assert "10:00:00" in c.get("/browse/2026/09/23").text
    assert "23/09/2026 21:15:30" not in c.get("/todo").text
    done = c.get("/done").text
    assert "Completati (1)" in done and "23/09/2026 21:15:30" in done
    assert f'hx-post="/captures/{cap.id}/undone"' in done and "checked" in done
    assert 'aria-label="Segna come da fare"' in done
    c.post(f"/captures/{cap.id}/undone", headers=htmx(recordings))
    assert "21:15:30" in c.get("/browse/2026/09/23").text and "Completati (0)" in c.get("/done").text


def test_tree_counts_only_open_notes(recordings, db, settings):
    arch = Archive(settings.archive_dir)
    library.set_done(db, arch, capture_by(db, "cap_20260810_070000").id, True, NOW)
    db.commit()
    assert catalog.month_counts(db, 2026) == [(9, 2)]                     # agosto sparisce
    assert catalog.day_counts(db, 2026, 9) == [(23, 2)]


def test_completed_are_ordered_by_completion(recordings, db, settings):
    from datetime import timedelta
    arch = Archive(settings.archive_dir)
    library.set_done(db, arch, capture_by(db, "cap_20260810_070000").id, True, NOW + timedelta(hours=1))
    library.set_done(db, arch, capture_by(db, CID).id, True, NOW)
    db.commit()
    assert [c.capture_id for c in catalog.list_done(db)] == ["cap_20260810_070000", CID]
    assert catalog.count_done(db) == 2


def test_menu_holds_all_sections_without_search(recordings):
    text = recordings.client.get("/todo").text
    assert '<details class="menu"' in text
    for label in ("Archivio", "Da fare (3)", "Completati (0)", "Tag", "Da sistemare", "Cestino",
                  "Dispositivi", "Impostazioni", "Esci"):
        assert f'aria-label="{label}"' in text, label
    assert 'href="/search" aria-label="Ricerca"' not in text
    assert '<span class="current">Da fare</span>' in text           # sezione corrente nel menu chiuso


def test_row_title_toggles_the_detail(recordings, db):
    cap = capture_by(db, CID)
    text = recordings.client.get("/browse/2026/09/23").text
    assert 'aria-expanded="false"' in text and f'aria-controls="detail-{cap.id}"' in text
    assert "toggleDetail(" in text
