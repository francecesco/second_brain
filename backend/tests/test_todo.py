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


def test_done_routes_return_the_row(recordings, db):
    cap = capture_by(db, CID)
    c = recordings.client
    assert c.post(f"/captures/{cap.id}/done").status_code == 403          # CSRF
    r = c.post(f"/captures/{cap.id}/done", headers=htmx(recordings))
    assert r.status_code == 200 and f'id="row-{cap.id}"' in r.text
    assert 'class="row done"' in r.text and f'hx-post="/captures/{cap.id}/undone"' in r.text
    assert 'aria-label="Segna come da fare"' in r.text
    r = c.post(f"/captures/{cap.id}/undone", headers=htmx(recordings))
    assert 'class="row"' in r.text and 'aria-label="Segna come fatta"' in r.text
    assert c.post("/captures/00000000-0000-0000-0000-000000000000/done",
                  headers=htmx(recordings)).status_code == 404


def test_todo_page_and_counter(recordings, db):
    c = recordings.client
    text = c.get("/todo").text
    assert "Da fare (3)" in text and 'aria-label="Da fare (3)"' in text
    assert "23/09/2026 21:15:30" in text and "10/08/2026 09:00:00" in text
    cap = capture_by(db, CID)
    c.post(f"/captures/{cap.id}/done", headers=htmx(recordings))
    text = c.get("/todo").text
    assert "Da fare (2)" in text and "23/09/2026 21:15:30" not in text
    assert "Da fare (1)" in c.get(f"/todo?device={DEV2}").text
    # nell'archivio resta, barrata; nei risultati per tag pure
    assert 'class="row done"' in c.get("/browse/2026/09/23").text


def test_on_the_todo_page_checking_removes_the_row(recordings, db):
    # Resta solo l'aggiornamento out-of-band del contatore: la riga sparisce dalla lista.
    cap = capture_by(db, CID)
    headers = htmx(recordings) | {"HX-Current-URL": f"http://test/todo?device={DEV}"}
    r = recordings.client.post(f"/captures/{cap.id}/done?from=todo", headers=headers)
    assert r.status_code == 200 and f'id="row-{cap.id}"' not in r.text
    assert 'id="nav-todo" class="on" href="/todo?device=' in r.text and "Da fare (1)" in r.text
    assert 'hx-swap-oob="outerHTML"' in r.text


def test_done_row_updates_the_counter_out_of_band(recordings, db):
    cap = capture_by(db, CID)
    r = recordings.client.post(f"/captures/{cap.id}/done", headers=htmx(recordings))
    assert 'id="nav-todo"' in r.text and "Da fare (2)" in r.text and 'hx-swap-oob="outerHTML"' in r.text


def test_row_title_toggles_the_detail(recordings, db):
    cap = capture_by(db, CID)
    text = recordings.client.get("/browse/2026/09/23").text
    assert 'aria-expanded="false"' in text and f'aria-controls="detail-{cap.id}"' in text
    assert "toggleDetail(" in text
