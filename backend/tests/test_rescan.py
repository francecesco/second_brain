from datetime import date

import pytest
from sqlalchemy import delete, select

from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.models import Capture, Device
from secondbrain.rescan import rescan
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
    assert "aggiunte 2" in capsys.readouterr().out
