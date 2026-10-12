"""Pagina /new (spec note scritte §3)."""
from sqlalchemy import select

from secondbrain.models import Capture
from tests.helpers import capture_by


def test_new_page_has_form_and_capture_id(ui):
    r = ui.client.get("/new")
    assert r.status_code == 200
    assert 'name="text"' in r.text and "autofocus" in r.text
    assert 'name="capture_id" value="cap_20260928_120000"' in r.text


def test_header_has_new_note_button(ui):
    assert 'href="/new"' in ui.client.get("/todo").text


def test_save_redirects_to_todo_and_creates_note(ui, db):
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": "Comprare il latte",
                                     "capture_id": "cap_20260928_120000"}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/todo")
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
    assert 'aria-current="page"' in ui.client.get("/new").text
    assert 'aria-current="page"' not in ui.client.get("/todo").text
