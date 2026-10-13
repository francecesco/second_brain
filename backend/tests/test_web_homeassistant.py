from dataclasses import replace

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from secondbrain import ha_store
from secondbrain import homeassistant as ha
from secondbrain import settings_store as store
from secondbrain.models import WebSession
from secondbrain.web.auth import set_password
from tests.helpers import NOW, PASSWORD, htmx
from tests.test_homeassistant import TOKEN, URL, hass


def form(ui, **data):
    return {"csrf": ui.csrf, **data}


def mock_http(client, handler):
    client.app.state.http.close()
    client.app.state.http = httpx.Client(transport=httpx.MockTransport(handler))


def connect(ui, url=URL + "/", token=TOKEN):
    return ui.client.post("/settings/homeassistant", data=form(ui, url=url, token=token),
                          follow_redirects=False)


def test_card_is_after_the_vocabulary(ui):
    text = ui.client.get("/settings").text
    assert 'id="homeassistant"' in text and "Connessione Home Assistant" in text
    assert text.index('id="vocabulary"') < text.index('id="homeassistant"')
    assert "preview.png" not in text  # nessuna lettura: niente anteprima


def test_save_connection_cleans_url_and_encrypts_token(ui, db):
    r = connect(ui)
    assert (r.status_code, r.headers["location"]) == (303, "/settings#homeassistant")
    db.expire_all()
    assert ha_store.get_config(db).url == URL
    assert TOKEN not in str(store.get_setting(db, ha_store.TOKEN_KEY, None))
    assert ha_store.refresh_requested_after(db, None)
    text = ui.client.get("/settings").text
    assert TOKEN not in text and f'value="{URL}"' in text
    assert "lascia vuoto per non cambiarlo" in text
    assert ha_store.token_status(db, store.SecretBox(ui.client.app.state.settings.settings_key)) in text


def test_bad_url_and_url_change_show_the_error_in_the_card(ui, db):
    r = connect(ui, url="ftp://ha.test")
    assert r.status_code == 400 and "indirizzo di Home Assistant non valido" in r.text
    assert 'id="homeassistant"' in r.text and TOKEN not in r.text
    connect(ui)
    r = connect(ui, url="http://altro.test:8123", token="")
    assert r.status_code == 400 and "Cambiando l&#39;URL va reinserito il token" in r.text
    db.expire_all()
    assert ha_store.get_config(db).url == URL


def test_actions_require_csrf(ui):
    c = ui.client
    for url in ("/settings/homeassistant", "/settings/homeassistant/test",
                "/settings/homeassistant/choices", "/settings/homeassistant/refresh",
                "/settings/homeassistant/delete"):
        assert c.post(url, data={"url": URL, "token": TOKEN}).status_code == 403, url


def test_prova_ok_and_401(ui):
    r = ui.client.post("/settings/homeassistant/test", headers=htmx(ui))
    assert 'class="test-result ko"' in r.text and "Nessuna connessione" in r.text
    connect(ui)
    mock_http(ui.client, hass)
    r = ui.client.post("/settings/homeassistant/test", headers=htmx(ui))
    assert 'class="test-result ok"' in r.text and "API running." in r.text
    mock_http(ui.client, lambda request: httpx.Response(401))
    r = ui.client.post("/settings/homeassistant/test", headers=htmx(ui))
    assert 'class="test-result ko"' in r.text and "401" in r.text and TOKEN not in r.text


def test_load_entities_with_saved_and_missing_choices(ui, db):
    connect(ui)
    ui.client.post("/settings/homeassistant/choices", data=form(
        ui, calendars=["calendar.casa", "calendar.sparito"], sensor_1="sensor.t_soggiorno",
        label_1="casa", shopping_list="todo.shopping_list"))
    mock_http(ui.client, hass)
    r = ui.client.get("/settings/homeassistant/entities", headers={"HX-Request": "true"})
    text = r.text
    assert r.status_code == 200 and TOKEN not in text
    assert 'name="calendars" value="calendar.casa" checked' in text
    assert 'name="calendars" value="calendar.differenziata">' in text
    assert 'name="calendars" value="calendar.sparito" checked' in text and "non trovata" in text
    assert 'name="sensor_1"' in text and 'name="sensor_6"' in text and 'name="sensor_7"' not in text
    assert 'value="sensor.t_soggiorno" selected' in text and 'name="label_1"' in text
    assert 'name="sensors"' not in text
    assert f'maxlength="{ha_store.MAX_LABEL_LEN}"' in text
    assert 'name="shopping_list" value="todo.shopping_list" checked' in text
    assert 'name="shopping_list" value=""' in text
    assert "light.cucina" not in text
    assert f'value="{ui.csrf}"' in text


def saved_sensors(ui, **rows):
    ui.client.post("/settings/homeassistant/choices", data=form(ui, **rows))


def rows_html(ui):
    mock_http(ui.client, hass)
    return ui.client.get("/settings/homeassistant/entities", headers={"HX-Request": "true"}).text


def test_sensor_rows_are_prefilled_in_saved_order(ui, db):
    connect(ui)
    saved_sensors(ui, sensor_1="sensor.umidita", label_1="umidita",
                  sensor_2="sensor.t_soggiorno", label_2="casa")
    text = rows_html(ui)
    row1 = text[text.index('name="sensor_1"'):text.index('name="sensor_2"')]
    row2 = text[text.index('name="sensor_2"'):text.index('name="sensor_3"')]
    assert 'value="sensor.umidita" selected' in row1 and 'value="umidita"' in row1
    assert 'value="sensor.t_soggiorno" selected' in row2 and 'value="casa"' in row2
    assert row1.count("selected") == 1 and 'aria-label="Sensore 1"' in text
    row3 = text[text.index('name="sensor_3"'):text.index('name="sensor_4"')]
    assert "selected" not in row3 and "— nessuno —" in row3 and "sensor.t_soggiorno" in row3


def test_missing_saved_sensor_is_an_extra_selected_option(ui, db):
    connect(ui)
    saved_sensors(ui, sensor_1="sensor.sparito", label_1="x")
    text = rows_html(ui)
    row1 = text[text.index('name="sensor_1"'):text.index('name="sensor_2"')]
    assert 'value="sensor.sparito" selected' in row1 and "non trovato" in row1
    assert "sensor.sparito" not in text[text.index('name="sensor_2"'):]


def test_two_rows_and_an_empty_one_keep_row_order(ui, db):
    connect(ui)
    saved_sensors(ui, sensor_1="sensor.umidita", label_1="u", sensor_2="",
                  label_2="orfana", sensor_4="sensor.t_soggiorno", label_4="")
    db.expire_all()
    assert ha_store.get_config(db).sensors == (ha.SensorChoice("sensor.umidita", "u"),
                                               ha.SensorChoice("sensor.t_soggiorno", None))


def test_duplicate_sensor_in_two_rows_is_saved_once(ui, db):
    connect(ui)
    saved_sensors(ui, sensor_1="sensor.umidita", label_1="prima",
                  sensor_2="sensor.umidita", label_2="seconda")
    db.expire_all()
    assert ha_store.get_config(db).sensors == (ha.SensorChoice("sensor.umidita", "prima"),)


def test_bad_sensor_in_a_row_saves_nothing(ui, db):
    connect(ui)
    saved_sensors(ui, sensor_1="sensor.umidita")
    r = ui.client.post("/settings/homeassistant/choices", data=form(
        ui, sensor_1="sensor.t_soggiorno", sensor_5="sensor.<x>"))
    assert r.status_code == 400
    db.expire_all()
    assert ha_store.get_config(db).sensors == (ha.SensorChoice("sensor.umidita", None),)


def test_load_entities_errors(ui):
    r = ui.client.get("/settings/homeassistant/entities")
    assert 'class="test-result ko"' in r.text and "Nessuna connessione" in r.text
    connect(ui)
    mock_http(ui.client, lambda request: httpx.Response(401))
    r = ui.client.get("/settings/homeassistant/entities")
    assert 'class="test-result ko"' in r.text and "401" in r.text and TOKEN not in r.text


def test_save_choices_with_labels(ui, db):
    connect(ui)
    r = ui.client.post("/settings/homeassistant/choices", data=form(
        ui, calendars=["calendar.casa"], sensor_1="sensor.t_soggiorno", label_1="  soggiorno  ",
        sensor_2="sensor.umidita", label_2="", shopping_list=""), follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/settings#homeassistant")
    db.expire_all()
    config = ha_store.get_config(db)
    assert config.calendars == ("calendar.casa",)
    assert config.sensors == (ha.SensorChoice("sensor.t_soggiorno", "soggiorno"),
                              ha.SensorChoice("sensor.umidita", None))
    assert config.shopping_list is None
    text = ui.client.get("/settings").text
    assert "calendar.casa" in text and "soggiorno" in text


def test_save_choices_rejects_bad_entity_ids(ui, db):
    connect(ui)
    for data in ({"calendars": ["calendar.casa<script>"]}, {"sensor_1": "Sensor.X"}, {"sensor_3": "calendar.casa"},
                 {"shopping_list": "todo.lista spesa"}, {"calendars": ["sensor.t_soggiorno"]}):
        r = ui.client.post("/settings/homeassistant/choices", data=form(ui, **data))
        assert r.status_code == 400, data
    db.expire_all()
    assert ha_store.get_config(db).calendars == ()


def test_save_choices_without_connection_is_refused(ui, db):
    r = ui.client.post("/settings/homeassistant/choices", data=form(ui, calendars=["calendar.casa"]))
    assert r.status_code == 409 and "Prima salva" in r.text
    db.expire_all()
    assert ha_store.get_config(db) is None


def test_refresh_now(ui, db):
    connect(ui)
    db.expire_all()
    ha_store.save_reading(db, ha.Reading((), (), (), ()), NOW)
    db.commit()
    assert not ha_store.refresh_requested_after(db, NOW)
    r = ui.client.post("/settings/homeassistant/refresh", data=form(ui), follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/settings#homeassistant")
    db.expire_all()
    assert ha_store.refresh_requested_after(db, NOW - timedelta(seconds=1))


def test_disconnect(ui, db):
    connect(ui)
    assert "Scollega" in ui.client.get("/settings").text
    r = ui.client.post("/settings/homeassistant/delete", data=form(ui), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert ha_store.get_config(db) is None
    assert store.get_setting(db, ha_store.TOKEN_KEY, None) is None


def test_page_shows_snapshot_and_preview(ui, db):
    connect(ui)
    db.expire_all()
    reading = ha.Reading(
        events=(ha.Event("calendar.casa", "Riunione", "2026-09-28T15:00:00+02:00",
                         "2026-09-28T16:00:00+02:00", False),),
        shopping=("latte", "pane"),
        sensors=(ha.SensorValue("sensor.t_soggiorno", "casa", "21.5", "°C"),),
        missing=())
    ha_store.save_reading(db, reading, NOW)
    db.commit()
    text = ui.client.get("/settings").text
    assert "Ultima lettura: 14:00 · 1 eventi · 2 articoli · 1 sensori" in text
    assert '<img src="/settings/homeassistant/preview.png' in text
    ha_store.save_error(db, "Home Assistant irraggiungibile", NOW)
    db.commit()
    assert "Home Assistant irraggiungibile" in ui.client.get("/settings").text


@pytest.fixture
def ui_without_key(make_client, settings, db):
    client = make_client(replace(settings, settings_key=None, allow_unauthenticated_lan=True))
    set_password(db, PASSWORD, NOW)
    db.commit()
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    return client, db.scalars(select(WebSession)).one().csrf_token


def test_without_settings_key(ui_without_key, db):
    client, csrf = ui_without_key
    text = client.get("/settings").text
    card = text[text.index('id="homeassistant"'):]
    assert "SETTINGS_KEY" in card
    assert 'name="token"' in card and card.index("disabled") < card.index("</form>")
    r = client.post("/settings/homeassistant", data={"csrf": csrf, "url": URL, "token": TOKEN},
                    follow_redirects=False)
    assert r.status_code == 409 and "SETTINGS_KEY" in r.text and TOKEN not in r.text
    db.expire_all()
    assert ha_store.get_config(db) is None
    r = client.post("/settings/homeassistant/test", headers={"X-CSRF-Token": csrf})
    assert 'class="test-result ko"' in r.text and "SETTINGS_KEY" in r.text

