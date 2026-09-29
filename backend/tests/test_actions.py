from datetime import timedelta

from secondbrain import library
from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.clock import utcnow
from secondbrain.models import Capture
from secondbrain.ota import publish
from tests.helpers import DEV, NOW, TEST_DB, capture_by, make_wav, upload

CID = "cap_20260923_191530"


def htmx(ui):
    return {"X-CSRF-Token": ui.csrf, "HX-Request": "true"}


def test_actions_require_csrf(recordings, db):
    cap = capture_by(db, CID)
    assert recordings.client.post(f"/captures/{cap.id}/title", data={"title": "x"}).status_code == 403
    assert recordings.client.post(f"/captures/{cap.id}/trash").status_code == 403


def test_title_returns_the_updated_row(recordings, db):
    cap = capture_by(db, CID)
    r = recordings.client.post(f"/captures/{cap.id}/title", data={"title": "Idea"}, headers=htmx(recordings))
    assert r.status_code == 200
    assert f'id="row-{cap.id}"' in r.text and "Idea" in r.text


def test_detail_shows_actions(recordings, db):
    cap = capture_by(db, CID)
    text = recordings.client.get(f"/captures/{cap.id}").text
    assert f'hx-post="/captures/{cap.id}/recorded-at"' in text
    assert 'value="2026-09-23T21:15:30"' in text


def test_correct_date_redirects_to_the_new_day(recordings, db):
    cap = capture_by(db, CID)
    url = f"/captures/{cap.id}/recorded-at"
    r = recordings.client.post(url, data={"recorded_at": "2026-09-20T18:05"}, headers=htmx(recordings))
    assert r.status_code == 200 and r.headers["hx-redirect"] == "/browse/2026/09/20"
    for bad in ["ieri", "", "1999-01-01T10:00", "2026-09-20T18:05+02:00"]:
        r = recordings.client.post(url, data={"recorded_at": bad}, headers=htmx(recordings))
        assert r.status_code == 400, bad


def test_trash_and_restore_routes(recordings, db):
    cap = capture_by(db, CID)
    c = recordings.client
    r = c.post(f"/captures/{cap.id}/trash", headers=htmx(recordings))
    assert r.status_code == 200 and r.text == ""
    assert "21:15:30" in c.get("/trash").text
    assert "21:15:30" not in c.get("/browse/2026/09/23").text
    r = c.post(f"/trash/{cap.id}/restore", data={"csrf": recordings.csrf}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/trash")
    assert "21:15:30" in c.get("/browse/2026/09/23").text


def test_delete_only_from_trash(recordings, db):
    # id catturato subito: dopo la cancellazione (fatta dalla sessione della richiesta,
    # diversa da `db`) anche solo leggere cap.id su un'istanza scaduta di `db` solleva
    # ObjectDeletedError, perché SQLAlchemy scade anche gli attributi di chiave primaria.
    cid = capture_by(db, CID).id
    c = recordings.client
    form = {"csrf": recordings.csrf}
    assert c.post(f"/trash/{cid}/delete", data=form, follow_redirects=False).status_code == 409
    c.post(f"/captures/{cid}/trash", headers=htmx(recordings))
    assert c.post(f"/trash/{cid}/delete", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Capture, cid) is None


def test_fix_lists_estimated_recordings(recordings):
    upload(recordings.client, make_wav(fill=b"\x09\x00"), capture_id="cap_unsynced_000001", ts=None)
    text = recordings.client.get("/fix").text
    assert "28/09/2026 14:00:00" in text and "Da sistemare (1)" in text
    assert 'aria-label="Da sistemare (1)"' in text and 'title="Da sistemare (1)"' in text


def test_devices_page_and_rename(recordings, db, settings, tmp_path):
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\xe9fw")
    publish(db, settings.firmware_dir, fw, "epaper154", "0.7.0", NOW)
    db.commit()
    c = recordings.client
    text = c.get("/devices").text
    assert DEV in text and "pubblicata 0.7.0" in text
    r = c.post(f"/devices/{DEV}/name", data={"name": "scrivania", "csrf": recordings.csrf},
               follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/devices")
    assert "scrivania" in c.get("/devices").text
    r = c.post(f"/devices/{DEV}/name", data={"name": "  ", "csrf": recordings.csrf})
    assert r.status_code == 400


def test_old_trash_is_purged_at_startup(recordings, db, settings, make_client):
    cid = capture_by(db, CID).id  # vedi nota in test_delete_only_from_trash
    library.trash_capture(db, Archive(settings.archive_dir), cid, NOW - timedelta(days=40))
    make_client(settings)
    db.expire_all()
    assert db.get(Capture, cid) is None


def test_cli_trash_purge(recordings, db, settings, monkeypatch, capsys):
    cap = capture_by(db, CID)
    # la CLI usa l'orologio vero, non FakeClock
    library.trash_capture(db, Archive(settings.archive_dir), cap.id, utcnow() - timedelta(days=40))
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    assert main(["trash", "purge"]) == 0
    assert "Eliminate definitivamente 1" in capsys.readouterr().out
