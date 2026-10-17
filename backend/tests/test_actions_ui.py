"""Azioni (spec azioni §2.2, §5): regole tra nota e azioni, spunte dalla UI, In scadenza."""
from datetime import date

import pytest

from secondbrain import catalog, library
from secondbrain.archive import Archive
from secondbrain.notefile import Action
from secondbrain.notes import note_from_capture
from secondbrain.vault import Vault
from secondbrain.web.groups import due_groups
from secondbrain.web.templating import due_label
from tests.ai_fakes import processed
from tests.helpers import NOW, capture_by, htmx

CID = "cap_20260923_191530"
TODAY = date(2026, 9, 28)                      # NOW a Roma
LATE = Action("aaaa0001", "Pagare il bollo", date(2026, 9, 25))
TODAY_A = Action("aaaa0002", "Chiamare Marco", TODAY)
SOON = Action("aaaa0003", "Scrivere a Stefano", date(2026, 10, 2))
FAR = Action("aaaa0004", "Rinnovare il dominio", date(2026, 11, 30))
NODUE = Action("aaaa0005", "Comprare il filtro")


@pytest.fixture
def note(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, actions=(LATE, TODAY_A, SOON, FAR, NODUE))
    return cid


def _arch(settings):
    return Archive(settings.archive_dir)


def _tick_all(db, settings, cid, done=True):
    for a in note_from_capture(capture_by(db, CID)).actions:
        library.set_action_done(db, _arch(settings), settings.tz_archive, cid, a.id, done, NOW)


# --- regole nota/azioni ---

def test_all_actions_done_completes_the_note_and_one_reopens_it(note, db, settings):
    library.set_action_done(db, _arch(settings), settings.tz_archive, note, LATE.id, True, NOW)
    assert capture_by(db, CID).done_at is None
    _tick_all(db, settings, note)
    cap = capture_by(db, CID)
    assert cap.done_at == NOW and "actions" in cap.edited
    assert _arch(settings).read_sidecar(cap.rel_path)["done_at"] == NOW.isoformat()
    library.set_action_done(db, _arch(settings), settings.tz_archive, note, SOON.id, False, NOW)
    assert capture_by(db, CID).done_at is None
    with pytest.raises(library.NotFound):
        library.set_action_done(db, _arch(settings), settings.tz_archive, note, "nessuna", True, NOW)


def test_ticking_the_note_ticks_the_actions_and_back(note, db, settings):
    library.set_action_done(db, _arch(settings), settings.tz_archive, note, LATE.id, True, NOW)
    library.set_done(db, _arch(settings), note, True, NOW, tz=settings.tz_archive)
    assert all(a.done for a in note_from_capture(capture_by(db, CID)).actions)
    library.set_done(db, _arch(settings), note, False, NOW, tz=settings.tz_archive)
    assert not any(a.done for a in note_from_capture(capture_by(db, CID)).actions)


def test_editing_keeps_ticks_by_id_and_a_new_action_reopens(note, db, settings):
    _tick_all(db, settings, note)
    library.edit_actions(db, _arch(settings), settings.tz_archive, note,
                         [Action(LATE.id, "Pagare il bollo auto", LATE.due), Action("", "Nuova")], NOW)
    cap = capture_by(db, CID)
    acts = note_from_capture(cap).actions
    assert [a.text for a in acts] == ["Pagare il bollo auto", "Nuova"]
    assert acts[0].done == NOW and acts[1].done is None and acts[1].id != ""
    assert cap.done_at is None                                   # c'e' di nuovo qualcosa da fare


def test_actions_are_written_in_the_vault(recordings, db, settings):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, actions=(LATE,))
    vault = Vault(settings.vault_dir)
    library.set_action_done(db, _arch(settings), settings.tz_archive, cid, LATE.id, True, NOW,
                            vault=vault)
    text = vault.read_text(capture_by(db, CID).note_path)
    assert "Pagare il bollo" in text and "completata: true" in text


def test_unprocessed_note_cannot_have_actions(recordings, db, settings):
    cid = capture_by(db, CID).id
    with pytest.raises(library.NotProcessed):
        library.edit_actions(db, _arch(settings), settings.tz_archive, cid, [Action("", "x")], NOW)


# --- In scadenza ---

def test_due_groups_and_labels(note, db):
    groups = due_groups(catalog.open_actions(db), TODAY)
    assert [(g.key, [a["text"] for _, a, _ in g.items]) for g in groups] == [
        ("overdue", ["Pagare il bollo"]), ("today", ["Chiamare Marco"]),
        ("soon", ["Scrivere a Stefano"])]
    assert due_label("2026-09-28", TODAY) == "oggi" and due_label("2026-09-29", TODAY) == "domani"
    assert due_label("2026-10-02", TODAY) == "ven 02/10" and due_label("2027-01-04", TODAY) == "lun 04/01/27"
    tiles = catalog.todo_tiles(db, None, TODAY)
    assert (tiles["actions"], tiles["due"], tiles["overdue"]) == (5, 2, 1)


def test_todo_page_shows_due_section_and_row_actions(note, recordings):
    text = recordings.client.get("/todo").text
    due = text.split('id="due"')[1].split("</section>")[0]
    assert "Scadute" in due and "Oggi" in due and "Prossimi 7 giorni" in due
    assert "Rinnovare il dominio" not in due and "Comprare il filtro" not in due
    assert 'class="due-tag late"' in due
    row = text.split(f'id="acts-{note}"')[1].split("</ul>")[0]
    assert row.count("<li") == 5 and f'/captures/{note}/actions/{LATE.id}/done' in row
    assert 'class="tile warn"' in text and "1 scaduta" in text


# --- route ---

def test_ticking_an_action_redraws_the_row_actions_and_the_rest(note, recordings, db):
    c = recordings.client
    url = f"/captures/{note}/actions/{LATE.id}/done"
    assert c.post(url).status_code == 403                                  # CSRF
    headers = htmx(recordings) | {"HX-Current-URL": "http://test/todo"}
    r = c.post(url, headers=headers)
    assert r.status_code == 200 and "HX-Retarget" not in r.headers
    assert f'id="acts-{note}"' in r.text and f'/actions/{LATE.id}/undone' in r.text
    assert 'id="tiles"' in r.text and 'id="due"' in r.text and 'id="tree"' in r.text
    assert c.post(f"/captures/{note}/actions/nessuna/done", headers=headers).status_code == 404
    assert c.post(f"/captures/{note}/actions/{LATE.id}/boh", headers=headers).status_code == 404


def test_last_action_moves_the_whole_row_out(note, recordings, db):
    c = recordings.client
    headers = htmx(recordings) | {"HX-Current-URL": "http://test/todo"}
    for a in (LATE, TODAY_A, SOON, FAR):
        c.post(f"/captures/{note}/actions/{a.id}/done", headers=headers)
    r = c.post(f"/captures/{note}/actions/{NODUE.id}/done", headers=headers)
    assert r.headers["HX-Retarget"] == f"#row-{note}" and r.headers["HX-Reswap"] == "outerHTML"
    assert f'id="acts-{note}"' not in r.text
    assert "Completati (1)" in r.text
    back = htmx(recordings) | {"HX-Current-URL": "http://test/done"}
    r = c.post(f"/captures/{note}/actions/{NODUE.id}/undone", headers=back)
    assert r.headers["HX-Retarget"] == f"#row-{note}"


def test_editing_actions_from_the_detail(note, recordings, db):
    c = recordings.client
    form = c.get(f"/captures/{note}/field/actions?edit=1").text
    assert 'hx-post="/captures/' in form and 'name="due"' in form and "<template>" in form
    headers = htmx(recordings) | {"HX-Current-URL": "http://test/todo"}
    r = c.post(f"/captures/{note}/actions", headers=headers,
               data={"id": [LATE.id, ""], "text": ["Pagare il bollo", "Nuova cosa"],
                     "due": ["2026-09-25", ""]})
    assert r.status_code == 200 and "Nuova cosa" in r.text and 'id="tiles"' in r.text
    acts = note_from_capture(capture_by(db, CID)).actions
    assert [a.text for a in acts] == ["Pagare il bollo", "Nuova cosa"]
    bad = c.post(f"/captures/{note}/actions", headers=headers, data={"id": ["x"], "text": []})
    assert bad.status_code == 400


def test_detail_shows_the_actions_field(note, recordings):
    text = recordings.client.get(f"/captures/{note}").text
    assert f'id="field-actions-{note}"' in text and "Azioni" in text
    assert text.index("field-summary") < text.index("field-actions") < text.index("field-tags")
