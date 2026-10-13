import httpx
import pytest
from datetime import datetime, timezone

from secondbrain import homeassistant as ha

TOKEN = "token-finto-per-i-test"
URL = "http://ha.test:8123"
STATES = [
    {"entity_id": "calendar.casa", "state": "off", "attributes": {"friendly_name": "Casa"}},
    {"entity_id": "calendar.differenziata", "state": "off", "attributes": {"friendly_name": "Raccolta"}},
    {"entity_id": "sensor.t_soggiorno", "state": "21.5",
     "attributes": {"friendly_name": "Temperatura soggiorno", "unit_of_measurement": "°C"}},
    {"entity_id": "todo.shopping_list", "state": "3", "attributes": {"friendly_name": "Spesa"}},
    {"entity_id": "light.cucina", "state": "on", "attributes": {}},
]


def fake(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def hass(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    path = request.url.path
    if path == "/api/":
        return httpx.Response(200, json={"message": "API running."})
    if path == "/api/states":
        return httpx.Response(200, json=STATES)
    if path == "/api/services/calendar/get_events":
        assert "return_response" in request.url.query.decode()
        body = __import__("json").loads(request.content)
        assert body["entity_id"] == ["calendar.casa"]
        return httpx.Response(200, json={"changed_states": [], "service_response": {
            "calendar.casa": {"events": [
                {"start": "2026-10-13T09:30:00+02:00", "end": "2026-10-13T10:30:00+02:00", "summary": "Riunione"},
                {"start": "2026-10-14", "end": "2026-10-15", "summary": "Carta"}]}}})
    if path == "/api/services/todo/get_items":
        return httpx.Response(200, json={"changed_states": [], "service_response": {
            "todo.shopping_list": {"items": [{"summary": "latte", "uid": "1", "status": "needs_action"},
                                             {"summary": "pane", "uid": "2", "status": "needs_action"}]}}})
    return httpx.Response(404)


START = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
CONFIG = ha.HaConfig(url=URL, calendars=("calendar.casa", "calendar.sparito"),
                     sensors=(ha.SensorChoice("sensor.t_soggiorno", "casa"), ha.SensorChoice("sensor.sparito")),
                     shopping_list="todo.shopping_list")


def test_clean_url():
    assert ha.clean_url(" http://ha.test:8123/ ") == "http://ha.test:8123"
    with pytest.raises(ha.HaError):
        ha.clean_url("ftp://ha.test")
    with pytest.raises(ha.HaError):
        ha.clean_url("")


def test_test_ok():
    assert ha.test(fake(hass), URL, TOKEN) == "API running."


def test_test_401_never_shows_the_token():
    with pytest.raises(ha.HaError) as exc:
        ha.test(fake(lambda r: httpx.Response(401)), URL, TOKEN)
    assert "401" in str(exc.value) and TOKEN not in str(exc.value)


def test_test_timeout_and_unreachable():
    def slow(request):
        raise httpx.ReadTimeout("lento", request=request)
    def down(request):
        raise httpx.ConnectError("giù", request=request)
    with pytest.raises(ha.HaError, match="timeout"):
        ha.test(fake(slow), URL, TOKEN)
    with pytest.raises(ha.HaError, match="irraggiungibile"):
        ha.test(fake(down), URL, TOKEN)


def test_list_entities_groups_by_domain():
    found = ha.list_entities(fake(hass), URL, TOKEN)
    assert [e.entity_id for e in found["calendar"]] == ["calendar.casa", "calendar.differenziata"]
    assert found["sensor"][0].name == "Temperatura soggiorno"
    assert [e.entity_id for e in found["todo"]] == ["todo.shopping_list"]
    assert set(found) == {"calendar", "sensor", "todo"}


def test_read_skips_missing_entities():
    reading = ha.read(fake(hass), CONFIG, TOKEN, START, END)
    assert [e.summary for e in reading.events] == ["Riunione", "Carta"]
    assert [e.all_day for e in reading.events] == [False, True]
    assert reading.shopping == ("latte", "pane")
    assert reading.sensors == (ha.SensorValue("sensor.t_soggiorno", "casa", "21.5", "°C"),)
    assert set(reading.missing) == {"calendar.sparito", "sensor.sparito"}


def test_read_old_home_assistant_without_service_response():
    def old(request):
        if request.url.path == "/api/states":
            return httpx.Response(200, json=STATES)
        return httpx.Response(200, json=[])     # vecchio formato: lista di stati cambiati
    with pytest.raises(ha.HaError, match="2023.12"):
        ha.read(fake(old), CONFIG, TOKEN, START, END)


def test_read_without_choices_makes_no_service_calls():
    calls = []
    def spy(request):
        calls.append(request.url.path)
        return hass(request)
    reading = ha.read(fake(spy), ha.HaConfig(url=URL), TOKEN, START, END)
    assert calls == ["/api/states"] and reading == ha.Reading((), (), (), ())


def test_ssl_error_has_its_own_message():
    def bad_cert(request):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request)
    with pytest.raises(ha.HaError, match="certificato"):
        ha.test(fake(bad_cert), URL, TOKEN)


def test_other_status_and_bad_url_scheme():
    with pytest.raises(ha.HaError, match="500"):
        ha.test(fake(lambda r: httpx.Response(500)), URL, TOKEN)
    with pytest.raises(ha.HaError):
        ha.clean_url("http://")
