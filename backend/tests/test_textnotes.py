"""Note scritte dall'app (spec note scritte): creazione, doppioni, titolo provvisorio."""
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from secondbrain import jobs, library, textnotes
from secondbrain import settings_store as store
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.models import Capture, Device, Job
from secondbrain.notes import effective_title
from secondbrain.rescan import rescan
from secondbrain.vault import Vault
from secondbrain.worker import Worker
from tests.ai_fakes import FakeFactory, FakeProvider, configure_providers
from tests.helpers import NOW

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260928_120000"


def create(db, settings, raw="Chiamare Costanza per Home Assistant", cid=CID, vault=True):
    return textnotes.create_text_note(
        db, Archive(settings.archive_dir), raw, cid, NOW, tz=ROME, language="it",
        vault=Vault(settings.vault_dir) if vault else None)


def test_normalize_text():
    assert textnotes.normalize_text("  riga 1\r\nriga 2\r\n ") == "riga 1\nriga 2"
    with pytest.raises(textnotes.TextNoteError):
        textnotes.normalize_text(" \r\n\t ")
    with pytest.raises(textnotes.TextNoteError):
        textnotes.normalize_text("x" * (textnotes.MAX_TEXT_NOTE_CHARS + 1))


def test_provisional_title():
    assert textnotes.provisional_title("\n\n  Comprare   il latte \naltro") == "Comprare il latte"
    long = "parola " * 20
    title = textnotes.provisional_title(long)
    assert len(title) <= textnotes.PROVISIONAL_TITLE_CHARS and title.endswith("…")
    assert not title[:-1].endswith(" ")
    assert textnotes.provisional_title("   ") is None


def test_new_capture_id():
    assert textnotes.new_capture_id(NOW) == "cap_20260928_120000"


def test_creates_files_catalog_note_and_enrich_job(db, settings):
    capture, created = create(db, settings)
    assert created
    archive = Archive(settings.archive_dir)
    assert capture.rel_path == "2026/09/28/140000_" + capture.device_id + ".txt"
    assert archive.abs(capture.rel_path).read_text() == "Chiamare Costanza per Home Assistant"
    assert archive.read_sidecar(capture.rel_path)["duration_s"] == 0
    assert (capture.transcript, capture.edited, capture.language) == (
        "Chiamare Costanza per Home Assistant", ["transcript"], "it")
    assert capture.duration_s == 0 and capture.date_estimated is False and capture.recorded_at == NOW
    assert "Chiamare Costanza" in Vault(settings.vault_dir).read_text(capture.note_path)
    job = db.get(Job, capture.id)
    assert (job.stage, job.status) == (jobs.STAGE_ENRICH, "queued")
    device = db.get(Device, capture.device_id)
    assert (device.type, device.name, device.token_hash) == ("web", "App", None)


def test_app_device_is_created_once(db, settings):
    a, _ = create(db, settings, cid="cap_20260928_120000")
    b, _ = create(db, settings, raw="altro", cid="cap_20260928_120001")
    assert a.device_id == b.device_id
    assert len(db.scalars(select(Device).where(Device.type == "web")).all()) == 1


def test_double_submit_returns_the_same_note(db, settings):
    a, created_a = create(db, settings, raw="ciao\r\nmondo")
    b, created_b = create(db, settings, raw="ciao\nmondo")
    assert (created_a, created_b) == (True, False) and a.id == b.id
    assert db.scalar(select(Capture).where(Capture.id != a.id)) is None


def test_same_id_different_text_is_a_new_note(db, settings):
    a, _ = create(db, settings, raw="uno")
    b, created = create(db, settings, raw="due")
    assert created and a.id != b.id and b.rel_path.endswith("_2.txt")


def test_invalid_capture_id_gets_a_fresh_one(db, settings):
    capture, _ = create(db, settings, cid="../../etc")
    assert capture.capture_id == "cap_20260928_120000"


def test_empty_text_creates_nothing(db, settings):
    with pytest.raises(textnotes.TextNoteError):
        create(db, settings, raw="  \n ")
    assert db.scalar(select(Capture)) is None
    assert not any(Archive(settings.archive_dir).root.rglob("*.txt"))


def test_failure_after_files_cleans_up(db, settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db giù")
    monkeypatch.setattr(jobs, "enqueue", boom)
    with pytest.raises(RuntimeError):
        create(db, settings)
    assert db.scalar(select(Capture)) is None
    root = Archive(settings.archive_dir).root
    assert not any(root.rglob("*.txt")) and not any(root.rglob("*.json"))
    assert not any(Vault(settings.vault_dir).root.rglob("*.md"))


def test_effective_title_uses_provisional_title_for_text_notes(db, settings):
    capture, _ = create(db, settings, raw="Comprare il latte\ne il pane", vault=False)
    assert effective_title(capture, ROME) == "Comprare il latte"
    capture.title_auto = "Spesa"
    assert effective_title(capture, ROME) == "Spesa"


def test_vault_file_name_uses_provisional_title(db, settings):
    capture, _ = create(db, settings, raw="Comprare il latte\ne il pane")
    assert capture.note_path.endswith("Comprare il latte.md")


def test_creation_waits_for_the_advisory_lock(db, settings):
    import threading
    from secondbrain.catalog import make_sessionmaker
    sm = make_sessionmaker(db.get_bind())
    done = threading.Event()
    result = {}

    def worker():
        with sm() as s2:
            result["r"] = textnotes.create_text_note(
                s2, Archive(settings.archive_dir), "ciao", CID, NOW, tz=ROME, language="it",
                vault=Vault(settings.vault_dir))
        done.set()

    with sm() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": textnotes.CREATE_LOCK_KEY})
        t = threading.Thread(target=worker)
        t.start()
        assert not done.wait(1.0)          # bloccata finche' il lock e' tenuto
        holder.commit()
    assert done.wait(10)
    t.join()
    assert result["r"][1] is True


def test_concurrent_double_submit_keeps_files(db, settings):
    import threading
    from secondbrain.catalog import make_sessionmaker
    sm = make_sessionmaker(db.get_bind())
    out, errors = [], []

    def worker():
        try:
            with sm() as s2:
                out.append(textnotes.create_text_note(
                    s2, Archive(settings.archive_dir), "ciao", CID, NOW, tz=ROME, language="it",
                    vault=Vault(settings.vault_dir)))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    assert sorted(created for _, created in out) == [False, True]
    assert len(db.scalars(select(Capture)).all()) == 1
    assert len(db.scalars(select(Device).where(Device.type == "web")).all()) == 1
    capture = out[0][0]
    archive = Archive(settings.archive_dir)
    assert archive.abs(capture.rel_path).exists()
    assert archive.read_sidecar(capture.rel_path)
    assert Vault(settings.vault_dir).read_text(capture.note_path)


@pytest.fixture
def worker_env(db, settings, clock):
    box = store.SecretBox(settings.settings_key)
    configure_providers(db, box, NOW)
    groq, gemini = FakeProvider("groq"), FakeProvider("gemini")
    worker = Worker(sessionmaker=make_sessionmaker(db.get_bind()),
                    archive=Archive(settings.archive_dir), tz=ROME, box=box,
                    factory=FakeFactory(groq=groq, gemini=gemini), clock=clock,
                    vault=Vault(settings.vault_dir))
    return worker, groq


def test_worker_enriches_a_text_note_without_transcribing(db, settings, worker_env):
    worker, groq = worker_env
    capture, _ = create(db, settings)
    assert worker.run_once()
    db.expire_all()
    capture = db.get(Capture, capture.id)
    assert capture.transcript == "Chiamare Costanza per Home Assistant"
    assert capture.title_auto and capture.processed_at
    assert [c[0] for c in groq.calls] == ["enrich"]


def test_reprocess_never_transcribes_a_text_note(db, settings, worker_env):
    worker, groq = worker_env
    capture, _ = create(db, settings)
    worker.run_once()
    library.reprocess(db, capture.id, NOW)
    db.commit()
    worker.run_once()
    assert [c[0] for c in groq.calls] == ["enrich", "enrich"]


def test_rescan_without_note_rebuilds_text_through_the_worker(db, settings, worker_env):
    worker, groq = worker_env
    capture, _ = create(db, settings)
    vault = Vault(settings.vault_dir)
    vault.delete(capture.note_path)                   # nota persa
    db.delete(db.get(Job, capture.id)); db.commit()
    report = rescan(db, Archive(settings.archive_dir), NOW, vault=vault)
    db.commit()
    assert report.queued == 1 and not [p for p in report.problems if ".txt" in p]
    assert worker.run_once()
    db.expire_all()
    capture = db.get(Capture, capture.id)
    assert capture.transcript == "Chiamare Costanza per Home Assistant"
    assert capture.edited == ["transcript"] and capture.title_auto
    assert "transcribe" not in [c[0] for c in groq.calls]


def test_trash_and_restore_keep_the_text_source(db, settings):
    capture, _ = create(db, settings, vault=False)
    archive = Archive(settings.archive_dir)
    library.trash_capture(db, archive, capture.id, NOW)
    library.restore_capture(db, archive, capture.id)
    db.commit()
    assert db.get(Capture, capture.id).rel_path.endswith(".txt")
    assert archive.abs(db.get(Capture, capture.id).rel_path).is_file()
