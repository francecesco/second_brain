from datetime import datetime, timezone

import pytest

from secondbrain import ha_store
from secondbrain import homeassistant as ha
from secondbrain import settings_store as store
from tests.helpers import NOW, TEST_SETTINGS_KEY

BOX = store.SecretBox(TEST_SETTINGS_KEY)
READING = ha.Reading(events=(ha.Event("calendar.casa", "Riunione", "2026-10-13T09:30:00+02:00",
                                      "2026-10-13T10:30:00+02:00", False),),
                     shopping=("latte",), sensors=(ha.SensorValue("sensor.t", "casa", "21.5", "°C"),),
                     missing=())


def test_connection_and_token_are_saved_encrypted(db):
    ha_store.save_connection(db, BOX, " http://ha.test:8123/ ", "token-finto-per-i-test")
    db.commit()
    assert ha_store.get_config(db).url == "http://ha.test:8123"
    raw = store.get_setting(db, ha_store.TOKEN_KEY, None)
    assert raw and "token-finto" not in raw
    assert ha_store.get_token(db, BOX) == "token-finto-per-i-test"
    assert "token-finto-per-i-test" not in ha_store.token_status(db, BOX)


def test_empty_token_keeps_the_old_one(db):
    ha_store.save_connection(db, BOX, "http://ha.test:8123", "token-finto-per-i-test")
    ha_store.save_connection(db, BOX, "http://altro.test:8123", "")
    assert ha_store.get_token(db, BOX) == "token-finto-per-i-test"
    assert ha_store.get_config(db).url == "http://altro.test:8123"


def test_without_settings_key_the_token_is_refused(db):
    with pytest.raises(store.NoSettingsKey):
        ha_store.save_connection(db, store.SecretBox(None), "http://ha.test:8123", "token-finto")


def test_choices_and_labels(db):
    ha_store.save_connection(db, BOX, "http://ha.test:8123", "token-finto")
    ha_store.save_choices(db, ["calendar.casa"], [ha.SensorChoice("sensor.t", "  casa\n "),
                                                 ha.SensorChoice("sensor.u", "")], "todo.spesa")
    cfg = ha_store.get_config(db)
    assert cfg.calendars == ("calendar.casa",)
    assert cfg.sensors == (ha.SensorChoice("sensor.t", "casa"), ha.SensorChoice("sensor.u", None))
    assert cfg.shopping_list == "todo.spesa"


def test_snapshot_roundtrip_and_error_keeps_data(db):
    assert ha_store.get_snapshot(db) == ha_store.Snapshot(None, None)
    ha_store.save_reading(db, READING, NOW)
    later = datetime(2026, 9, 28, 12, 5, tzinfo=timezone.utc)
    ha_store.save_error(db, "Home Assistant non risponde (timeout)", later)
    snap = ha_store.get_snapshot(db)
    assert snap.reading == READING and snap.fetched_at == NOW
    assert (snap.error, snap.error_at) == ("Home Assistant non risponde (timeout)", later)
    ha_store.save_reading(db, READING, later)
    assert ha_store.get_snapshot(db).error is None


def test_broken_snapshot_reads_as_empty(db):
    store.set_setting(db, ha_store.SNAPSHOT_KEY, {"fetched_at": "rotto", "reading": 3})
    assert ha_store.get_snapshot(db) == ha_store.Snapshot(None, None)


def test_refresh_request(db):
    assert not ha_store.refresh_requested_after(db, None)
    ha_store.request_refresh(db, NOW)
    assert ha_store.refresh_requested_after(db, None)
    assert not ha_store.refresh_requested_after(db, NOW)


def test_clear(db):
    ha_store.save_connection(db, BOX, "http://ha.test:8123", "token-finto")
    ha_store.save_reading(db, READING, NOW)
    ha_store.clear(db)
    assert ha_store.get_config(db) is None and ha_store.get_token(db, BOX) is None
    assert ha_store.get_snapshot(db).reading is None
