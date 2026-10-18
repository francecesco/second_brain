from datetime import timedelta

from secondbrain import library
from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.clock import utcnow
from secondbrain.models import Capture
from secondbrain.ota import publish
from tests.helpers import DEV, NOW, TEST_DB, capture_by, htmx, make_wav, upload

CID = "cap_20260923_191530"


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


# --- firmware dalla pagina Dispositivi (spec firmware-ui §3) ---

def _image(version):
    from tests.test_ota import make_image
    return make_image(version)


def test_devices_page_firmware_card_without_releases(recordings):
    text = recordings.client.get("/devices").text
    assert 'id="firmware"' in text
    assert "nessuna release" in text
    assert 'action="/devices/firmware/epaper154"' in text


def test_publish_firmware_from_the_devices_page(recordings, db, settings):
    c = recordings.client
    r = c.post("/devices/firmware/epaper154", data={"csrf": recordings.csrf},
               files={"file": ("secondbrain_fw.bin", _image("0.9.1"), "application/octet-stream")},
               follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/devices?pubblicata=0.9.1#firmware")
    text = c.get(r.headers["location"]).text
    assert "Pubblicata 0.9.1" in text and "corrente" in text
    assert (settings.firmware_dir / "epaper154" / "secondbrain-0.9.1.bin").read_bytes() == _image("0.9.1")
    # la tabella dei dispositivi mostra la release pubblicata accanto al firmware del device
    assert "pubblicata 0.9.1" in text
    # il manifest per il device la espone
    from tests.test_ota import auth
    from secondbrain.devices import create_device
    _, tok = create_device(db, "001122334455", "nuovo", "epaper154", NOW)
    db.commit()
    assert c.get("/firmware/manifest.json", headers=auth(tok)).json()["version"] == "0.9.1"


def test_publish_firmware_rejects_bad_files(recordings, settings):
    c = recordings.client
    r = c.post("/devices/firmware/epaper154", data={"csrf": recordings.csrf},
               files={"file": ("x.bin", b"\xe9non-un-firmware" * 50, "application/octet-stream")},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/devices?errore=")
    assert "non è un firmware" in c.get(r.headers["location"]).text
    assert not (settings.firmware_dir / "epaper154").exists()
    # due volte la stessa versione
    for _ in range(2):
        r = c.post("/devices/firmware/epaper154", data={"csrf": recordings.csrf},
                   files={"file": ("fw.bin", _image("0.9.1"), "application/octet-stream")},
                   follow_redirects=False)
    assert "errore=" in r.headers["location"]
    assert "già pubblicata" in c.get(r.headers["location"]).text
    # tipo sconosciuto e file mancante
    r = c.post("/devices/firmware/../x", data={"csrf": recordings.csrf},
               files={"file": ("fw.bin", _image("0.9.2"), "application/octet-stream")},
               follow_redirects=False)
    assert r.status_code in (303, 404)
    r = c.post("/devices/firmware/epaper154", data={"csrf": recordings.csrf}, follow_redirects=False)
    assert r.status_code in (303, 422)


def test_rollback_firmware_from_the_devices_page(recordings, db, settings):
    c = recordings.client
    publish(db, settings.firmware_dir, _write(settings, "0.9.0"), "epaper154", "0.9.0", NOW)
    publish(db, settings.firmware_dir, _write(settings, "0.9.1"), "epaper154", "0.9.1", NOW)
    db.commit()
    r = c.post("/devices/firmware/epaper154/rollback", data={"csrf": recordings.csrf},
               follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/devices?corrente=0.9.0#firmware")
    text = c.get(r.headers["location"]).text
    assert "Release corrente: 0.9.0" in text
    # senza una precedente il pulsante è disabilitato e la richiesta dà errore
    assert "disabled" in text.split('id="firmware"', 1)[1]
    r = c.post("/devices/firmware/epaper154/rollback", data={"csrf": recordings.csrf},
               follow_redirects=False)
    assert "errore=" in r.headers["location"]


def test_firmware_routes_require_csrf(recordings):
    c = recordings.client
    assert c.post("/devices/firmware/epaper154",
                  files={"file": ("fw.bin", _image("0.9.1"), "application/octet-stream")}).status_code == 403
    assert c.post("/devices/firmware/epaper154/rollback").status_code == 403


def _write(settings, version):
    settings.firmware_dir.mkdir(parents=True, exist_ok=True)
    p = settings.firmware_dir / f"src-{version}.bin"
    p.write_bytes(_image(version))
    return p
