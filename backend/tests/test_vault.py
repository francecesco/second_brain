from datetime import datetime

from secondbrain.vault import Vault, clean_title_for_name, note_dir, vault_note_name

LOCAL = datetime(2026, 10, 7, 11, 58, 29)


def test_note_name_is_date_time_and_clean_title():
    assert vault_note_name(LOCAL, "Promemoria per le piante") == "2026-10-07 11.58 Promemoria per le piante.md"
    assert note_dir(LOCAL) == "Captures/2026"


def test_title_cleaning_removes_forbidden_characters_and_truncates():
    assert clean_title_for_name('Bolletta: luce/gas "casa" <ok>?') == "Bolletta luce gas casa ok"
    assert clean_title_for_name("  tanti   spazi  .") == "tanti spazi"
    assert len(clean_title_for_name("x" * 200)) == 80
    assert clean_title_for_name("") == "Nota"           # mai un nome vuoto
    assert clean_title_for_name("è \U0001F331") == "è \U0001F331"  # accenti ed emoji restano


def test_write_rename_list_and_unique(tmp_path):
    vault = Vault(tmp_path)
    rel = vault.unique_rel("Captures/2026", "2026-10-07 11.58 Piante.md")
    assert rel == "Captures/2026/2026-10-07 11.58 Piante.md"
    mtime = vault.write_text(rel, "---\nid: x\n---\n")
    assert (tmp_path / rel).read_text() == "---\nid: x\n---\n" and mtime == vault.mtime(rel)
    assert vault.unique_rel("Captures/2026", "2026-10-07 11.58 Piante.md") == "Captures/2026/2026-10-07 11.58 Piante (2).md"
    vault.rename(rel, "Captures/2026/2026-10-07 11.58 Fiori.md")
    assert not (tmp_path / rel).exists() and vault.mtime("Captures/2026/2026-10-07 11.58 Fiori.md")
    (tmp_path / "Captures/2026/.syncthing.x.md.tmp").write_text("x")
    (tmp_path / "Captures/2026/2026-10-07 11.58 Fiori.sync-conflict-20261007-120000-ABCDEF.md").write_text("x")
    (tmp_path / "Captures/Note mia.md").write_text("x")
    assert [r for r, _ in vault.list_notes()] == ["Captures/2026/2026-10-07 11.58 Fiori.md",
                                                  "Captures/Note mia.md"]
    assert vault.conflicts() == ["Captures/2026/2026-10-07 11.58 Fiori.sync-conflict-20261007-120000-ABCDEF.md"]
    vault.delete("Captures/2026/2026-10-07 11.58 Fiori.md")
    assert vault.mtime("Captures/2026/2026-10-07 11.58 Fiori.md") is None
