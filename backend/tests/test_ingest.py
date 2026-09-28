import errno
import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from secondbrain import ingest
from secondbrain.archive import Archive
from secondbrain.models import Capture, Device
from starlette.requests import Request

from secondbrain.httputil import is_lan_request
from tests.helpers import DEV, DEV2, INTERNET_CLIENT, NOW, capture_headers, make_wav, upload

BASE = "211530_70041dd8263c"


def captures(db):
    db.expire_all()
    return list(db.scalars(select(Capture).order_by(Capture.rel_path)))


def files(settings, day="2026/09/23"):
    directory = settings.archive_dir / day
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


def incoming(settings):
    return list((settings.archive_dir / ".incoming").iterdir())


def test_accepted(client, db, settings, token):
    wav = make_wav(1.5)
    r = upload(client, wav, token=token)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "accepted"
    assert body["path"] == f"2026/09/23/{BASE}.wav"
    assert (settings.archive_dir / body["path"]).read_bytes() == wav
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]
    [cap] = captures(db)
    assert str(cap.id) == body["id"]
    assert (cap.day, cap.date_estimated, cap.size_bytes) == (date(2026, 9, 23), False, len(wav))
    assert cap.recorded_at == datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)
    assert cap.duration_s == pytest.approx(1.5)
    sidecar = json.loads((settings.archive_dir / f"2026/09/23/{BASE}.json").read_text())
    assert sidecar["sha256"] == cap.sha256 and sidecar["id"] == body["id"]
    device = db.get(Device, DEV)
    assert (device.last_seen_at, device.last_firmware, device.last_power_source) == (NOW, "0.6.2", "usb")
    assert incoming(settings) == []


def test_duplicate_returns_409(client, db, settings, token):
    first = upload(client, token=token).json()
    r = upload(client, token=token)
    assert r.status_code == 409
    assert r.json() == {"id": first["id"], "status": "duplicate"}
    assert len(captures(db)) == 1
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]
    assert incoming(settings) == []


def test_same_id_with_different_content_is_archived_too(client, db, token):
    upload(client, make_wav(fill=b"\x01\x00"), token=token)
    r = upload(client, make_wav(fill=b"\x02\x00"), token=token)
    assert r.status_code == 201
    assert r.json()["path"] == f"2026/09/23/{BASE}_2.wav"


def test_unsynced_capture_gets_estimated_date(client, db, token):
    r = upload(client, token=token, capture_id="cap_unsynced_000001", ts=None)
    assert r.json()["path"] == "2026/09/28/140000_70041dd8263c.wav"
    [cap] = captures(db)
    assert cap.date_estimated and cap.recorded_at == NOW


def test_implausible_timestamp_is_estimated(client, db, token):
    upload(client, token=token, ts="2000-01-01T00:00:05Z")
    assert captures(db)[0].date_estimated


def test_dst_fall_back_keeps_both_recordings(client, clock, token):
    clock.now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    a = upload(client, make_wav(fill=b"\x01\x00"), token=token,
               capture_id="cap_20261025_003000", ts="2026-10-25T00:30:00Z")
    b = upload(client, make_wav(fill=b"\x02\x00"), token=token,
               capture_id="cap_20261025_013000", ts="2026-10-25T01:30:00Z")
    assert [a.json()["path"], b.json()["path"]] == [
        "2026/10/25/023000_70041dd8263c.wav", "2026/10/25/023000_70041dd8263c_2.wav"]


def test_two_devices_in_the_same_second(lan_client, settings):
    assert upload(lan_client).status_code == 201
    assert upload(lan_client, device=DEV2).status_code == 201
    assert [n for n in files(settings) if n.endswith(".wav")] == [
        "211530_70041dd8263c.wav", "211530_aabbccddeeff.wav"]


@pytest.mark.parametrize("kw", [{}, {"token": "sbagliato"}])
def test_token_required(client, token, kw):
    assert upload(client, **kw).status_code == 401


def test_token_of_another_device(client, token):
    assert upload(client, token=token, device=DEV2).status_code == 403


def test_lan_mode_registers_unknown_device(lan_client, db):
    assert upload(lan_client).status_code == 201
    assert db.get(Device, DEV) is not None


def test_lan_mode_refuses_internet_clients(make_client, settings):
    c = make_client(replace(settings, allow_unauthenticated_lan=True), client_addr=INTERNET_CLIENT)
    assert upload(c).status_code == 401


def test_lan_mode_refuses_requests_through_the_tunnel(lan_client):
    r = upload(lan_client, extra={"Cf-Connecting-Ip": "203.0.113.7"})
    assert r.status_code == 401


def _request(client, headers=()):
    return Request({"type": "http", "client": client,
                    "headers": [(k.encode(), v.encode()) for k, v in headers]})


@pytest.mark.parametrize("client,headers,expected", [
    (("192.168.1.5", 1), (), True),
    (("10.0.0.2", 1), (), True),
    (("172.18.0.3", 1), (), True),          # rete interna di Docker
    (("127.0.0.1", 1), (), True),
    (("::ffff:192.168.1.5", 1), (), True),
    (("203.0.113.7", 1), (), False),
    (("192.168.1.5", 1), (("cf-connecting-ip", "203.0.113.7"),), False),
    (("testclient", 1), (), False),
    (None, (), False),
])
def test_is_lan_request(client, headers, expected):
    assert is_lan_request(_request(client, headers)) is expected


def test_invalid_capture_id(client, token):
    assert upload(client, token=token, capture_id="../../x").status_code == 400


def test_too_large(make_client, settings, token):
    small = make_client(replace(settings, max_upload_bytes=1024))
    assert upload(small, token=token).status_code == 413


def test_missing_content_length(client, token):
    r = client.post("/captures", content=iter([make_wav()]), headers=capture_headers(token=token))
    assert r.status_code == 411


def test_not_a_wav(client, db, settings, token):
    assert upload(client, b"non e' audio" * 10, token=token).status_code == 422
    assert captures(db) == [] and files(settings) == [] and incoming(settings) == []


def test_trashed_capture_still_counts_as_duplicate(client, db, token):
    upload(client, token=token)
    [cap] = captures(db)
    cap.trashed_at = NOW
    db.commit()
    assert upload(client, token=token).status_code == 409


def test_failed_commit_removes_archived_files(client, db, settings, token, monkeypatch):
    def boom(session):
        raise OperationalError("COMMIT", {}, Exception("db giù"))

    monkeypatch.setattr(ingest, "_commit", boom)
    assert upload(client, token=token).status_code == 503
    assert files(settings) == [] and captures(db) == [] and incoming(settings) == []
    monkeypatch.undo()
    assert upload(client, token=token).status_code == 201
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]


def test_ambiguous_commit_failure_keeps_files(client, db, settings, token, monkeypatch):
    def commit_then_lose_connection(session):
        session.commit()  # il salvataggio va a buon fine sul serio...
        raise OperationalError("COMMIT", {}, Exception("connessione persa dopo il commit"))

    monkeypatch.setattr(ingest, "_commit", commit_then_lose_connection)
    r = upload(client, token=token)
    assert r.status_code == 503  # ...ma il server non lo sa, e non deve buttare via i file
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]
    [cap] = captures(db)
    monkeypatch.undo()
    retry = upload(client, token=token)
    assert retry.status_code == 409
    assert retry.json()["id"] == str(cap.id)


def test_disk_full_returns_507(client, settings, token, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Archive, "commit_capture", full)
    assert upload(client, token=token).status_code == 507
    assert incoming(settings) == []


def test_sidecar_write_failure_leaves_no_orphans(client, settings, token, monkeypatch):
    """ENOSPC nel rename del sidecar (non nell'intera commit_capture): né il sidecar
    orfano nella cartella del giorno né il temporaneo in .incoming devono restare."""
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("secondbrain.archive.os.replace", full)
    assert upload(client, token=token).status_code == 507
    assert files(settings) == [] and incoming(settings) == []


def test_incoming_is_cleaned_at_startup(make_client, settings):
    inc = settings.archive_dir / ".incoming"
    inc.mkdir(parents=True)
    (inc / "vecchio.tmp").write_bytes(b"x")
    make_client(settings)
    assert list(inc.iterdir()) == []


def test_device_hostname_only_serves_device_paths(make_client, settings, token):
    c = make_client(replace(settings, device_hostname="ingest.example.org"))
    assert c.get("/healthz").status_code == 200
    for host in ["ingest.example.org", "ingest.example.org:443", "INGEST.example.org",
                 "ingest.example.org."]:
        assert c.get("/healthz", headers={"Host": host}).status_code == 404
    r = c.post("/captures", content=make_wav(),
               headers=capture_headers(token=token, extra={"Host": "ingest.example.org"}))
    assert r.status_code == 201
