import pytest

from secondbrain.archive import Archive, ArchiveError

DAY = "2026/09/28"


@pytest.fixture
def archive(tmp_path):
    a = Archive(tmp_path / "archive")
    a.ensure()
    return a


def put(archive, rel, content=b"x"):
    path = archive.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_commit_capture(archive):
    tmp = archive.new_incoming()
    tmp.write_bytes(b"RIFF-dati")
    rel = archive.commit_capture(tmp, DAY, "091530_dev", {"a": 1})
    assert rel == f"{DAY}/091530_dev.wav"
    assert archive.abs(rel).read_bytes() == b"RIFF-dati"
    assert archive.read_sidecar(rel) == {"a": 1}
    assert list(archive.incoming_dir.iterdir()) == []


def test_reserve_base_skips_taken_names(archive):
    put(archive, f"{DAY}/091530_dev.wav")
    put(archive, f"{DAY}/091530_dev_2.json")
    assert archive.reserve_base(DAY, "091530_dev") == "091530_dev_3"
    assert archive.reserve_base("2026/09/29", "091530_dev") == "091530_dev"


def test_related_files_only_same_base(archive):
    for name in ["x.wav", "x.json", "x.transcript.md", "x_2.wav", "xy.wav"]:
        put(archive, f"{DAY}/{name}")
    assert archive.related_files(f"{DAY}/x.wav") == ["x.json", "x.transcript.md", "x.wav"]


def test_move_carries_derived_files_and_prunes_empty_dirs(archive):
    for name in ["x.wav", "x.json", "x.transcript.md"]:
        put(archive, f"{DAY}/{name}")
    new = archive.move(f"{DAY}/x.wav", "2026/10/01")
    assert new == "2026/10/01/x.wav"
    assert archive.related_files(new) == ["x.json", "x.transcript.md", "x.wav"]
    assert not (archive.root / "2026/09").exists()


def test_move_renames_every_related_file_on_collision(archive):
    for name in ["x.wav", "x.json", "x.transcript.md"]:
        put(archive, f"{DAY}/{name}")
    put(archive, "2026/10/01/x.wav")
    new = archive.move(f"{DAY}/x.wav", "2026/10/01")
    assert new == "2026/10/01/x_2.wav"
    assert archive.related_files(new) == ["x_2.json", "x_2.transcript.md", "x_2.wav"]


def test_move_to_same_dir_is_noop(archive):
    put(archive, f"{DAY}/x.wav")
    assert archive.move(f"{DAY}/x.wav", DAY) == f"{DAY}/x.wav"


def test_trash_round_trip_keeps_trash_root(archive):
    put(archive, f"{DAY}/x.wav")
    in_trash = archive.move(f"{DAY}/x.wav", f".trash/{DAY}")
    assert in_trash == f".trash/{DAY}/x.wav"
    archive.delete(in_trash)
    assert archive.trash_dir.is_dir()
    assert not (archive.trash_dir / "2026").exists()


def test_delete_removes_related_files(archive):
    for name in ["x.wav", "x.json", "x_2.wav"]:
        put(archive, f"{DAY}/{name}")
    archive.delete(f"{DAY}/x.wav")
    assert sorted(p.name for p in (archive.root / DAY).iterdir()) == ["x_2.wav"]


@pytest.mark.parametrize("rel", ["", "..", "../x.wav", "/etc/passwd", "2026/../../x.wav", "a\\b"])
def test_abs_rejects_paths_outside_archive(archive, rel):
    with pytest.raises(ArchiveError):
        archive.abs(rel)


def test_clean_incoming(archive):
    archive.new_incoming().write_bytes(b"a")
    archive.new_incoming().write_bytes(b"b")
    assert archive.clean_incoming() == 2
    assert archive.clean_incoming() == 0


def test_write_sidecar_overwrites_without_leftovers(archive):
    put(archive, f"{DAY}/x.wav")
    archive.write_sidecar(f"{DAY}/x.wav", {"v": 1})
    archive.write_sidecar(f"{DAY}/x.wav", {"v": 2})
    assert archive.read_sidecar(f"{DAY}/x.wav") == {"v": 2}
    assert list(archive.incoming_dir.iterdir()) == []


def test_iter_sidecars_and_orphan_wavs(archive):
    for rel in [f"{DAY}/a.wav", f"{DAY}/a.json", f"{DAY}/a.summary.json",
                ".trash/2026/09/27/b.wav", ".trash/2026/09/27/b.json",
                f"{DAY}/c.wav", ".incoming/z.json"]:
        put(archive, rel)
    assert list(archive.iter_sidecar_rels()) == [".trash/2026/09/27/b.wav", f"{DAY}/a.wav"]
    assert list(archive.iter_wavs_without_sidecar()) == [f"{DAY}/c.wav"]
