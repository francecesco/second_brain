import hashlib

import pytest

from secondbrain.cli import main
from secondbrain.ota import OtaError, current_release, list_releases, publish, rollback
from tests.helpers import NOW, TEST_DB

FW = b"\xe9firmware-di-prova" * 100


@pytest.fixture
def fw_file(tmp_path):
    path = tmp_path / "secondbrain_fw.bin"
    path.write_bytes(FW)
    return path


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_publish(db, settings, fw_file):
    release = publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    assert release.current and release.file == "secondbrain-0.7.0.bin"
    assert (settings.firmware_dir / "epaper154" / release.file).read_bytes() == FW
    assert release.sha256 == hashlib.sha256(FW).hexdigest()


def test_new_release_becomes_the_only_current(db, settings, fw_file):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.1", NOW)
    assert current_release(db, "epaper154").version == "0.7.1"
    assert [(r.version, r.current) for r in list_releases(db)] == [("0.7.0", False), ("0.7.1", True)]


@pytest.mark.parametrize("type_,version", [("epaper154", "0.7"), ("epaper154", "v0.7.0"), ("../x", "0.7.0")])
def test_publish_validates(db, settings, fw_file, type_, version):
    with pytest.raises(OtaError):
        publish(db, settings.firmware_dir, fw_file, type_, version, NOW)


def test_publish_rejects_duplicates_and_missing_files(db, settings, fw_file, tmp_path):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    with pytest.raises(OtaError, match="già pubblicata"):
        publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    with pytest.raises(OtaError, match="non trovato"):
        publish(db, settings.firmware_dir, tmp_path / "manca.bin", "epaper154", "0.7.1", NOW)


def test_rollback(db, settings, fw_file):
    with pytest.raises(OtaError, match="nessuna release"):
        rollback(db, "epaper154")
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.1", NOW)
    assert rollback(db, "epaper154").version == "0.7.0"
    assert current_release(db, "epaper154").version == "0.7.0"
    with pytest.raises(OtaError, match="precedente"):
        rollback(db, "epaper154")


def test_manifest_404_without_release(client, token):
    assert client.get("/firmware/manifest.json", headers=auth(token)).status_code == 404


def test_manifest_and_binary(client, db, settings, fw_file, token):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    r = client.get("/firmware/manifest.json", headers=auth(token))
    assert r.status_code == 200
    assert r.json() == {
        "version": "0.7.0",
        "url": "http://testserver/firmware/epaper154/secondbrain-0.7.0.bin",
        "sha256": hashlib.sha256(FW).hexdigest(),
    }
    assert client.get("/firmware/epaper154/manifest.json", headers=auth(token)).json() == r.json()
    binary = client.get("/firmware/epaper154/secondbrain-0.7.0.bin", headers=auth(token))
    assert binary.status_code == 200 and binary.content == FW


def test_manifest_url_behind_the_tunnel(client, db, settings, fw_file, token):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    headers = auth(token) | {"Host": "ingest.example.org", "X-Forwarded-Proto": "https"}
    url = client.get("/firmware/manifest.json", headers=headers).json()["url"]
    assert url == "https://ingest.example.org/firmware/epaper154/secondbrain-0.7.0.bin"


@pytest.mark.parametrize("path", [
    "/firmware/epaper154/secondbrain-9.9.9.bin",
    "/firmware/epaper154/..%2F..%2Fetc%2Fpasswd",
    "/firmware/..%2F..%2Fx/secondbrain-0.7.0.bin",
    "/firmware/epaper154/manifest.bin",
])
def test_binary_not_found(client, db, settings, fw_file, token, path):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    assert client.get(path, headers=auth(token)).status_code == 404


def test_firmware_requires_token(client):
    assert client.get("/firmware/manifest.json").status_code == 401


def test_firmware_open_in_lan_mode(lan_client):
    assert lan_client.get("/firmware/manifest.json").status_code == 404  # nessuna release
    tunnel = {"Cf-Connecting-Ip": "203.0.113.7"}
    assert lan_client.get("/firmware/manifest.json", headers=tunnel).status_code == 401


def test_cli_publish_list_rollback(monkeypatch, db, settings, fw_file, capsys):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("FIRMWARE_DIR", str(settings.firmware_dir))
    assert main(["firmware", "publish", str(fw_file), "--version", "0.7.0"]) == 0
    assert main(["firmware", "publish", str(fw_file), "--version", "0.7.1"]) == 0
    assert main(["firmware", "rollback"]) == 0
    assert "0.7.0" in capsys.readouterr().out
    assert main(["firmware", "list"]) == 0
    out = capsys.readouterr().out
    assert "* epaper154 0.7.0" in out and "  epaper154 0.7.1" in out
    assert main(["firmware", "publish", str(fw_file), "--version", "0.7.0"]) == 1
