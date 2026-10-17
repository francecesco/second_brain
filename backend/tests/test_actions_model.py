"""Azioni (spec azioni §3): modello puro nel `.md`, catalogo e rescan."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain.archive import Archive
from secondbrain.notefile import (MAX_ACTION_LEN, MAX_ACTIONS, Action, Note, NoteError,
                                  actions_from_data, apply_actions, apply_enrichment,
                                  clean_actions, edit_actions, merge_edits, parse_note,
                                  render_note, sane_due, set_action_done, set_all_actions_done,
                                  validate_actions)
from secondbrain.notes import note_from_capture
from secondbrain.rescan import rescan
from secondbrain.vault import Vault
from tests.ai_fakes import processed
from tests.helpers import NOW, capture_by

ROME = ZoneInfo("Europe/Rome")
AT = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
A1 = Action("aaaa0001", "Chiamare Marco", date(2026, 10, 23))
A2 = Action("aaaa0002", "Comprare il filtro")
CID = "cap_20260923_191530"


def test_actions_round_trip_in_the_frontmatter():
    note = Note(transcript="testo", title="T", actions=(A1, replace_done(A2, AT)))
    text = render_note(note, ROME)
    assert "actions:" in text and "due: '2026-10-23'" in text
    assert parse_note(text) == note


def replace_done(action, at):
    from dataclasses import replace
    return replace(action, done=at)


def test_note_without_actions_has_no_actions_key():
    assert "actions" not in render_note(Note(transcript="x", title="T"), ROME)


def test_hand_written_actions_get_stable_ids():
    text = "---\nactions:\n- Chiamare Marco\n- {text: Pagare, due: 2026-11-02, done: true}\n---\n\nx\n"
    first, second = parse_note(text), parse_note(text)
    assert first.actions == second.actions
    assert [a.text for a in first.actions] == ["Chiamare Marco", "Pagare"]
    assert first.actions[1].due == date(2026, 11, 2) and first.actions[1].done is not None
    assert len({a.id for a in first.actions}) == 2


@pytest.mark.parametrize("front", ["actions: chiamare", "actions:\n- [1, 2]"])
def test_unreadable_actions_are_a_note_error(front):
    with pytest.raises(NoteError):
        parse_note(f"---\n{front}\n---\n\nx\n")


def test_clean_actions_trims_dedups_and_limits():
    raw = [Action("x", "  Riga\nuno  "), Action("y", "riga UNO"), Action("z", "   ")]
    raw += [Action(f"i{i}", f"a{i}") for i in range(20)]
    raw += [Action("l", "x" * 500)]
    out = clean_actions(raw)
    assert out[0].text == "Riga uno" and len(out) == MAX_ACTIONS
    assert all(len(a.text) <= MAX_ACTION_LEN for a in out)
    assert len({a.id for a in clean_actions([A1, Action(A1.id, "altro")])}) == 2


def test_validate_actions_from_the_ai():
    assert validate_actions({"title": "T"}) is None                    # chiave mancante
    assert validate_actions({"actions": "boh"}) == ()
    got = validate_actions({"actions": [
        {"text": "Chiamare Marco", "due": "2026-10-23"}, {"text": "Pagare", "due": "venerdì"},
        "Scrivere a Stefano", 3, {"due": "2026-10-23"}]})
    assert [(a.text, a.due) for a in got] == [
        ("Chiamare Marco", date(2026, 10, 23)), ("Pagare", None), ("Scrivere a Stefano", None)]
    assert all(a.done is None and len(a.id) == 8 for a in got)


def test_absurd_due_dates_are_dropped():
    acts = (Action("a", "x", date(2024, 1, 1)), Action("b", "y", date(2026, 10, 20)),
            Action("c", "z", date(2040, 1, 1)))
    assert [a.due for a in sane_due(acts, date(2026, 10, 17))] == [None, date(2026, 10, 20), None]


def test_enrichment_respects_edited_actions():
    note = Note(transcript="x", actions=(A1,))
    fresh = (A2,)
    assert apply_enrichment(note, "T", None, (), provider="p", model="m", at=AT,
                            actions=fresh).actions == (A2,)
    assert apply_enrichment(note, "T", None, (), provider="p", model="m", at=AT,
                            actions=None).actions == (A1,)            # provider senza azioni
    edited = edit_actions(note, (A1,))
    assert "actions" in edited.edited
    assert apply_actions(edited, fresh).actions == (A1,)


def test_ticking_is_an_edit_and_all_done_helpers():
    note = Note(transcript="x", actions=(A1, A2))
    ticked = set_action_done(note, A1.id, AT)
    assert ticked.actions[0].done == AT and "actions" in ticked.edited
    with pytest.raises(KeyError):
        set_action_done(note, "nessuno", AT)
    every = set_all_actions_done(ticked, NOW)
    assert every.actions[0].done == AT and every.actions[1].done == NOW   # la prima resta com'era
    assert all(a.done is None for a in set_all_actions_done(every, None).actions)
    assert set_all_actions_done(Note(transcript="x"), AT) == Note(transcript="x")


def test_obsidian_edit_of_actions_becomes_a_correction():
    note = Note(transcript="x", actions=(A1,))
    file_note = Note(transcript="x", actions=(A1, A2))
    merged, _, changed = merge_edits(file_note, None, note, None)
    assert changed and merged.actions == (A1, A2) and "actions" in merged.edited


def test_actions_from_catalog_data_survive_none():
    assert actions_from_data(None) == ()


def test_catalog_and_rescan_keep_actions(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, actions=(A1, A2))
    cap = capture_by(db, CID)
    assert cap.actions[0] == {"id": A1.id, "text": "Chiamare Marco", "due": "2026-10-23", "done": None}
    assert note_from_capture(cap).actions == (A1, A2)
    cap.actions = []                       # catalogo disallineato: vince il `.md`
    db.commit()
    rescan(db, Archive(settings.archive_dir), NOW, vault=Vault(settings.vault_dir))
    db.commit()
    assert note_from_capture(capture_by(db, CID)).actions == (A1, A2)
