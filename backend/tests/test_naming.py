from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain import naming

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
DEV = "70041dd8263c"


@pytest.mark.parametrize("cid", [
    "cap_20260923_191530", "cap_20260923_191530_2",
    "cap_unsynced_000042", "cap_unsynced_000042_3",
])
def test_valid_capture_ids(cid):
    assert naming.is_valid_capture_id(cid)


@pytest.mark.parametrize("cid", [
    "", "cap_2026_1915", "cap_unsynced_42", "../etc/passwd",
    "cap_20260923_191530.wav", "CAP_20260923_191530", "cap_20260923_191530\n",
])
def test_invalid_capture_ids(cid):
    assert not naming.is_valid_capture_id(cid)


def test_device_ids():
    assert naming.is_valid_device_id(DEV)
    for bad in ["", "70:04:1d:d8:26:3c", "70041DD8263C", "70041dd8263", "../x"]:
        assert not naming.is_valid_device_id(bad)


def test_device_types():
    assert naming.is_valid_device_type("epaper154")
    assert naming.is_valid_device_type("mic-v2_b")
    for bad in ["", "E-paper", "../x", "a" * 33, "-lead"]:
        assert not naming.is_valid_device_type(bad)


def test_ts_valid():
    assert naming.parse_capture_ts("2026-09-23T19:15:30Z", NOW) == \
        datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)


def test_ts_up_to_24h_in_future_is_accepted():
    assert naming.parse_capture_ts("2026-09-29T12:00:00Z", NOW) is not None


@pytest.mark.parametrize("raw", [
    None, "", "garbage", "2026-09-23 19:15:30", "2026-09-23T19:15:30+02:00",
    "2000-01-01T00:00:05Z", "2023-12-31T23:59:59Z", "2026-09-29T12:00:01Z",
])
def test_ts_rejected(raw):
    assert naming.parse_capture_ts(raw, NOW) is None


def test_resolve_recorded_at():
    assert naming.resolve_recorded_at(None, NOW) == (NOW, True)
    assert naming.resolve_recorded_at("2000-01-01T00:00:05Z", NOW) == (NOW, True)
    ts = datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)
    assert naming.resolve_recorded_at("2026-09-23T19:15:30Z", NOW) == (ts, False)


def test_midnight_uses_local_day():
    utc = datetime(2026, 9, 27, 22, 30, 0, tzinfo=UTC)  # 00:30 del 28 a Roma
    assert naming.local_day(utc, ROME) == date(2026, 9, 28)
    assert naming.day_dir(naming.local_day(utc, ROME)) == "2026/09/28"
    assert naming.base_name(utc, ROME, DEV) == "003000_70041dd8263c"


def test_winter_offset():
    utc = datetime(2026, 12, 1, 8, 15, 30, tzinfo=UTC)
    assert naming.base_name(utc, ROME, DEV) == "091530_70041dd8263c"


def test_dst_fall_back_same_local_time_gets_suffix():
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)   # 02:30 CEST
    second = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)  # 02:30 CET
    a = naming.base_name(first, ROME, DEV)
    b = naming.base_name(second, ROME, DEV)
    assert a == b == "023000_70041dd8263c"
    assert naming.unique_base(b, {a}.__contains__) == "023000_70041dd8263c_2"


def test_parse_day_dir():
    assert naming.parse_day_dir("2026/09/28") == date(2026, 9, 28)
    for bad in ["2026/9/28", "2026/13/01", "2026/02/30", "../2026/09/28", "2026-09-28"]:
        with pytest.raises(ValueError):
            naming.parse_day_dir(bad)


def test_unique_base():
    assert naming.unique_base("x", lambda b: False) == "x"
    assert naming.unique_base("x", {"x", "x_2"}.__contains__) == "x_3"
    assert naming.with_suffix("x", 1) == "x"
    assert naming.with_suffix("x", 4) == "x_4"
