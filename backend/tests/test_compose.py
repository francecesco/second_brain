"""Pagina /new (spec note scritte §3)."""
from sqlalchemy import select

from secondbrain import jobs
from secondbrain.archive import Archive
from secondbrain.models import Capture
from tests.helpers import capture_by


def test_new_page_has_form_and_capture_id(ui):
    r = ui.client.get("/new")
    assert r.status_code == 200
    assert 'name="text"' in r.text and "autofocus" in r.text
    assert 'name="capture_id" value="cap_20260928_120000"' in r.text


def test_cancel_drops_the_draft(ui):
    """"Annulla" toglie la bozza: la prossima "Nuova nota" non ripropone il testo scartato."""
    r = ui.client.get("/new")
    assert 'id="compose-cancel"' in r.text
    assert 'getElementById("compose-cancel").addEventListener("click"' in r.text


def test_header_has_new_note_button(ui):
    assert 'href="/new"' in ui.client.get("/todo").text


def test_save_redirects_to_todo_and_creates_note(ui, db):
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": "Comprare il latte",
                                     "capture_id": "cap_20260928_120000"}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/todo#salvata")
    page = ui.client.get("/todo").text
    assert "Comprare il latte" in page and "3 parole" in page
    assert "/audio" not in page


def test_empty_text_is_422_and_keeps_nothing(ui, db):
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": "   ",
                                     "capture_id": "cap_20260928_120000"})
    assert r.status_code == 422 and "La nota è vuota." in r.text
    assert db.scalar(select(Capture)) is None


def test_too_long_text_is_422_and_keeps_the_text(ui):
    text = "a" * 20_001
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": text, "capture_id": "cap_20260928_120000"})
    assert r.status_code == 422 and text in r.text


def test_csrf_required(ui):
    r = ui.client.post("/new", data={"csrf": "sbagliato", "text": "x"})
    assert r.status_code == 403


def test_login_required(lan_client):
    assert lan_client.get("/new", follow_redirects=False).status_code == 303


def test_detail_of_text_note_has_no_player_and_audio_is_404(ui, db):
    ui.client.post("/new", data={"csrf": ui.csrf, "text": "ciao", "capture_id": "cap_20260928_120000"})
    capture = capture_by(db, "cap_20260928_120000")
    assert "<audio" not in ui.client.get(f"/captures/{capture.id}").text
    assert ui.client.get(f"/captures/{capture.id}/audio").status_code == 404


def test_trash_has_no_listen_link_for_text_note(ui, db):
    ui.client.post("/new", data={"csrf": ui.csrf, "text": "ciao", "capture_id": "cap_20260928_120000"})
    capture = capture_by(db, "cap_20260928_120000")
    ui.client.post(f"/captures/{capture.id}/trash", headers={"X-CSRF-Token": ui.csrf})
    page = ui.client.get("/trash").text
    assert "ciao" in page and "/audio" not in page


def test_new_note_button_is_current_on_new_page(ui):
    assert 'class="new-note on" href="/new" aria-current="page"' in ui.client.get("/new").text
    todo = ui.client.get("/todo").text
    assert 'class="new-note" href="/new" aria-label' in todo and 'href="/new" aria-current' not in todo


def _form(ui, text="Comprare il latte"):
    return {"csrf": ui.csrf, "text": text, "capture_id": "cap_20260928_120000"}


def test_disk_error_is_500_and_keeps_the_text(ui, db, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disco pieno")
    monkeypatch.setattr(Archive, "commit_capture", broken)
    r = ui.client.post("/new", data=_form(ui))
    assert r.status_code == 500 and "Comprare il latte" in r.text and "disco?" in r.text
    assert db.scalar(select(Capture)) is None


def test_unexpected_error_is_500_and_keeps_the_text(ui, db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database giù")
    monkeypatch.setattr(jobs, "enqueue", broken)
    r = ui.client.post("/new", data=_form(ui))
    assert r.status_code == 500 and "Comprare il latte" in r.text and "Salvataggio non riuscito" in r.text
    assert db.scalar(select(Capture)) is None


def test_double_submit_creates_one_note(ui, db):
    first = ui.client.post("/new", data=_form(ui), follow_redirects=False)
    second = ui.client.post("/new", data=_form(ui), follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert len(db.scalars(select(Capture)).all()) == 1


def test_draft_is_cleared_only_after_a_confirmed_save(ui):
    """La bozza si toglie solo col segno del redirect riuscito, non su qualunque pagina."""
    page = ui.client.get("/login").text
    assert 'location.hash === "#salvata"' in page and "sb-draft-sent" not in page
