"""Vault Obsidian (spec vault §4-6): la cartella Captures/ con le note delle registrazioni."""
from pathlib import Path

CAPTURES_DIR = "Captures"


class Vault:
    def __init__(self, root: Path):
        self.root = root

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def ensure(self) -> None:
        (self.root / CAPTURES_DIR).mkdir(parents=True, exist_ok=True)
