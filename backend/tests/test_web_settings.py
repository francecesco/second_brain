from dataclasses import replace
from datetime import date

import httpx
import pytest
from sqlalchemy import delete, select

from secondbrain import jobs
from secondbrain import settings_store as store
from secondbrain.models import AiProvider, Job, WebSession
from secondbrain.usage import record_usage
from secondbrain.web.auth import set_password
from tests.ai_fakes import Recorder
from tests.helpers import NOW, PASSWORD, htmx

KEY = "gsk_test_secret_0123456789abcdef"
MODELS = {"data": [{"id": "whisper-large-v3-turbo"}, {"id": "openai/gpt-oss-120b"}]}


def form(ui, **data):
    return {"csrf": ui.csrf, **data}


def save(client, csrf, name="groq", **over):
    data = {"csrf": csrf, "api_key": KEY, "transcribe_model": "whisper-large-v3-turbo",
            "text_model": "openai/gpt-oss-120b", "enabled": "1"} | over
    return client.post(f"/settings/providers/{name}", data=data, follow_redirects=False)


def mock_http(ui, recorder):
    ui.client.app.state.http.close()
    ui.client.app.state.http = httpx.Client(transport=httpx.MockTransport(recorder))


def test_page_lists_the_providers_with_their_defaults(ui):
    text = ui.client.get("/settings").text
    assert text.index("Groq") < text.index("Gemini") < text.index("OpenAI")
    for model in ("whisper-large-v3-turbo", "openai/gpt-oss-120b", "gemini-3.8-flash",
                  "gpt-4o-mini-transcribe"):
        assert f'value="{model}"' in text
    assert "principale" in text and "riserva 1" in text and "nessuna chiave" in text
    assert 'aria-label="Impostazioni"' in text


def test_saved_key_is_only_ever_shown_masked(ui, db):
    r = save(ui.client, ui.csrf)
    assert (r.status_code, r.headers["location"]) == (303, "/settings")
    text = ui.client.get("/settings").text
    assert KEY not in text and "gsk_…def" in text
    db.expire_all()
    assert KEY not in db.get(AiProvider, "groq").api_key_enc
    save(ui.client, ui.csrf, api_key="")  # campo vuoto: la chiave resta
    assert "gsk_…def" in ui.client.get("/settings").text


def test_settings_actions_require_csrf(ui):
    c = ui.client
    for url in ("/settings/general", "/settings/providers/groq", "/settings/providers/groq/test",
                "/settings/providers/groq/move", "/settings/providers/groq/key/delete",
                "/settings/backfill"):
        assert c.post(url, data={"api_key": KEY}).status_code == 403, url


def test_unknown_provider_is_404(ui):
    assert save(ui.client, ui.csrf, name="acme").status_code == 404


def test_prova_lists_the_models_with_the_saved_key(ui):
    save(ui.client, ui.csrf)
    rec = Recorder(httpx.Response(200, json=MODELS))
    mock_http(ui, rec)
    r = ui.client.post("/settings/providers/groq/test", headers=htmx(ui))
    assert 'class="test-result ok"' in r.text and "whisper-large-v3-turbo" in r.text
    assert str(rec.requests[0].url) == "https://api.groq.com/openai/v1/models"
    assert rec.requests[0].headers["authorization"] == f"Bearer {KEY}"


def test_prova_error_never_shows_the_key(ui):
    save(ui.client, ui.csrf)
    mock_http(ui, Recorder(httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}})))
    r = ui.client.post("/settings/providers/groq/test", headers=htmx(ui))
    assert 'class="test-result ko"' in r.text and KEY not in r.text and "HTTP 401" in r.text


def test_prova_without_a_key(ui):
    r = ui.client.post("/settings/providers/gemini/test", headers=htmx(ui))
    assert "Nessuna chiave salvata" in r.text


def test_order_moves_up_and_down(ui, db):
    c = ui.client
    c.post("/settings/providers/gemini/move", data=form(ui, direction="up"))
    db.expire_all()
    assert [r.name for r in store.list_providers(db)] == ["gemini", "groq", "openai"]
    c.post("/settings/providers/gemini/move", data=form(ui, direction="down"))
    db.expire_all()
    assert [r.name for r in store.list_providers(db)] == ["groq", "gemini", "openai"]
    assert c.post("/settings/providers/gemini/move", data=form(ui, direction="x")).status_code == 400


def test_pause_and_language(ui, db):
    c = ui.client
    c.post("/settings/general", data=form(ui, language="en"))  # casella non spuntata = pausa
    db.expire_all()
    assert (store.is_paused(db), store.get_language(db)) == (True, "en")
    c.post("/settings/general", data=form(ui, language="it", active="1"))
    db.expire_all()
    assert (store.is_paused(db), store.get_language(db)) == (False, "it")
    assert c.post("/settings/general", data=form(ui, language="xx")).status_code == 400


def test_remove_key(ui, db):
    save(ui.client, ui.csrf)
    ui.client.post("/settings/providers/groq/key/delete", data=form(ui))
    db.expire_all()
    assert db.get(AiProvider, "groq").api_key_enc is None


def test_backfill_button(recordings, db):
    db.execute(delete(Job))
    db.commit()
    c = recordings.client
    assert "Elabora le note senza trascrizione (3)" in c.get("/settings").text
    r = c.post("/settings/backfill", data=form(recordings), follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/settings?queued=3")
    assert "3 note messe in coda." in c.get(r.headers["location"]).text
    assert {j.priority for j in db.scalars(select(Job))} == {jobs.PRIORITY_LOW}


def test_usage_and_queue(recordings, db):
    record_usage(db, "groq", date(2026, 9, 1), 90.0)
    db.commit()
    text = recordings.client.get("/settings").text
    assert "Questo mese: 1,5 min di audio · 1 chiamate" in text
    assert "3 in coda · 0 in corso · 0 fallite" in text


@pytest.fixture
def ui_without_key(make_client, settings, db):
    client = make_client(replace(settings, settings_key=None, allow_unauthenticated_lan=True))
    set_password(db, PASSWORD, NOW)
    db.commit()
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    return client, db.scalars(select(WebSession)).one().csrf_token


def test_without_settings_key(ui_without_key):
    client, csrf = ui_without_key
    text = client.get("/settings").text
    assert "SETTINGS_KEY" in text and "secondbrain gen-key" in text
    assert save(client, csrf).status_code == 409
    assert save(client, csrf, api_key="", transcribe_model="altro").status_code == 303
