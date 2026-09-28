"""Filesystem dell'archivio (spec §4): scrittura atomica, sidecar, spostamenti.

Un file è correlato a una registrazione se si chiama `<base>.<qualcosa>`; il nome base
non contiene punti, quindi `x.wav` e `x_2.wav` non si confondono mai.
"""
import glob
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

from .naming import unique_base

INCOMING = ".incoming"
TRASH = ".trash"
SIDECAR_SUFFIX = ".json"


class ArchiveError(ValueError):
    """Percorso non valido per l'archivio."""


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _is_base_file(path: Path) -> bool:
    return path.name.count(".") == 1


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    @property
    def incoming_dir(self) -> Path:
        return self.root / INCOMING

    @property
    def trash_dir(self) -> Path:
        return self.root / TRASH

    def ensure(self) -> None:
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def abs(self, rel: str) -> Path:
        if not rel or rel.startswith("/") or "\\" in rel:
            raise ArchiveError(f"percorso non valido: {rel!r}")
        path = (self.root / rel).resolve()
        if self.root not in path.parents:
            raise ArchiveError(f"percorso fuori dall'archivio: {rel!r}")
        return path

    def new_incoming(self) -> Path:
        self.ensure()
        return self.incoming_dir / f"{uuid.uuid4().hex}.tmp"

    def clean_incoming(self) -> int:
        if not self.incoming_dir.is_dir():
            return 0
        removed = 0
        for path in self.incoming_dir.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
        return removed

    def _taken(self, directory: Path, base: str) -> bool:
        return any(directory.glob(glob.escape(base) + ".*"))

    def reserve_base(self, dir_rel: str, base: str) -> str:
        directory = self.abs(dir_rel)
        return unique_base(base, lambda b: self._taken(directory, b))

    def commit_capture(self, tmp: Path, dir_rel: str, base: str, sidecar: dict) -> str:
        directory = self.abs(dir_rel)
        directory.mkdir(parents=True, exist_ok=True)
        rel_wav = f"{dir_rel}/{base}.wav"
        self.write_sidecar(rel_wav, sidecar)
        os.replace(tmp, directory / f"{base}.wav")
        _fsync_dir(directory)
        return rel_wav

    def write_sidecar(self, rel_wav: str, data: dict) -> None:
        target = self.abs(rel_wav).with_suffix(SIDECAR_SUFFIX)
        self.ensure()
        tmp = self.incoming_dir / f"{uuid.uuid4().hex}.json.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def read_sidecar(self, rel_wav: str) -> dict:
        with open(self.abs(rel_wav).with_suffix(SIDECAR_SUFFIX), encoding="utf-8") as f:
            return json.load(f)

    def related_files(self, rel_wav: str) -> list[str]:
        path = self.abs(rel_wav)
        pattern = glob.escape(path.stem) + ".*"
        return sorted(p.name for p in path.parent.glob(pattern) if p.is_file())

    def move(self, rel_wav: str, dest_dir_rel: str) -> str:
        src = self.abs(rel_wav)
        src_dir, base = src.parent, src.stem
        dest_dir = self.abs(dest_dir_rel)
        if dest_dir == src_dir:
            return rel_wav
        names = self.related_files(rel_wav)
        dest_dir.mkdir(parents=True, exist_ok=True)
        new_base = unique_base(base, lambda b: self._taken(dest_dir, b))
        for name in names:
            os.replace(src_dir / name, dest_dir / (new_base + name[len(base):]))
        _fsync_dir(dest_dir)
        self._prune(src_dir)
        return f"{dest_dir_rel}/{new_base}.wav"

    def delete(self, rel_wav: str) -> None:
        path = self.abs(rel_wav)
        for name in self.related_files(rel_wav):
            (path.parent / name).unlink()
        self._prune(path.parent)

    def _prune(self, directory: Path) -> None:
        keep = {self.root, self.trash_dir, self.incoming_dir}
        while directory not in keep and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent

    def iter_sidecar_rels(self) -> Iterator[str]:
        """Percorso relativo del WAV per ogni sidecar `<base>.json`, cestino compreso."""
        for path in sorted(self.root.rglob("*" + SIDECAR_SUFFIX)):
            rel = path.relative_to(self.root)
            if rel.parts[0] == INCOMING or not path.is_file() or not _is_base_file(path):
                continue
            yield rel.with_suffix(".wav").as_posix()

    def iter_wavs_without_sidecar(self) -> Iterator[str]:
        for path in sorted(self.root.rglob("*.wav")):
            rel = path.relative_to(self.root)
            if rel.parts[0] == INCOMING or not _is_base_file(path):
                continue
            if not path.with_suffix(SIDECAR_SUFFIX).exists():
                yield rel.as_posix()
