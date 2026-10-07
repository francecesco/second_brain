"""Vault Obsidian (spec vault §4-6): la cartella Captures/ con le note delle registrazioni.

Nome del file = nome della nota in Obsidian: `<AAAA-MM-GG> <HH.MM> <titolo>.md` in
`Captures/<anno>/`. Scritture atomiche nella stessa cartella; i temporanei iniziano con un
punto, cosi' Syncthing e `list_notes` li ignorano.
"""
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

CAPTURES_DIR = "Captures"
NOTE_SUFFIX = ".md"
MAX_NAME_TITLE_LEN = 80
FALLBACK_TITLE = "Nota"
FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SPACES = re.compile(r"\s+")
CONFLICT_MARK = ".sync-conflict-"
SKIP_PREFIXES = (".syncthing.", ".")
SKIP_SUFFIXES = (".tmp", "~")


def clean_title_for_name(title: str) -> str:
    text = SPACES.sub(" ", FORBIDDEN.sub(" ", title or "")).strip().rstrip(".").strip()
    return text[:MAX_NAME_TITLE_LEN].rstrip() or FALLBACK_TITLE


def note_dir(recorded_local: datetime) -> str:
    return f"{CAPTURES_DIR}/{recorded_local:%Y}"


def vault_note_name(recorded_local: datetime, title: str) -> str:
    return f"{recorded_local:%Y-%m-%d %H.%M} {clean_title_for_name(title)}{NOTE_SUFFIX}"


class Vault:
    def __init__(self, root: Path):
        self.root = root

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def ensure(self) -> None:
        (self.root / CAPTURES_DIR).mkdir(parents=True, exist_ok=True)

    def mtime(self, rel: str) -> float | None:
        try:
            return self.abs(rel).stat().st_mtime
        except FileNotFoundError:
            return None

    def read_text(self, rel: str) -> str:
        return self.abs(rel).read_text(encoding="utf-8")

    def unique_rel(self, dir_rel: str, name: str) -> str:
        stem, k = name.removesuffix(NOTE_SUFFIX), 1
        candidate = name
        while self.abs(f"{dir_rel}/{candidate}").exists():
            k += 1
            candidate = f"{stem} ({k}){NOTE_SUFFIX}"
        return f"{dir_rel}/{candidate}"

    def write_text(self, rel: str, text: str) -> float:
        target = self.abs(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".secondbrain-", suffix=NOTE_SUFFIX, dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target.stat().st_mtime

    def rename(self, old_rel: str, new_rel: str) -> None:
        target = self.abs(new_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.abs(old_rel), target)

    def delete(self, rel: str) -> None:
        self.abs(rel).unlink(missing_ok=True)

    def _is_note(self, path: Path) -> bool:
        name = path.name
        return (path.is_file() and name.endswith(NOTE_SUFFIX) and CONFLICT_MARK not in name
                and not name.startswith(SKIP_PREFIXES) and not name.endswith(SKIP_SUFFIXES))

    def list_notes(self) -> list[tuple[str, float]]:
        """Note sotto Captures/ con la data di modifica, senza temporanei e conflitti."""
        base = self.root / CAPTURES_DIR
        if not base.is_dir():
            return []
        found = [(p.relative_to(self.root).as_posix(), p.stat().st_mtime)
                 for p in base.rglob("*" + NOTE_SUFFIX) if self._is_note(p)]
        return sorted(found)

    def conflicts(self) -> list[str]:
        base = self.root / CAPTURES_DIR
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*" + NOTE_SUFFIX)
                      if p.is_file() and CONFLICT_MARK in p.name)
