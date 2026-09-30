from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from secondbrain.config import ConfigError, load_settings

BASE = {"DATABASE_URL": "postgresql+psycopg://u:p@h/db"}


def test_defaults():
    s = load_settings(BASE)
    assert s.database_url == BASE["DATABASE_URL"]
    assert s.archive_dir == Path("/data/archive")
    assert s.firmware_dir == Path("/data/firmware")
    assert s.tz_archive == ZoneInfo("Europe/Rome")
    assert s.max_upload_bytes == 32 * 1024 * 1024
    assert s.trash_retention_days == 30
    assert s.allow_unauthenticated_lan is False
    assert s.device_hostname is None
    assert s.settings_key is None


def test_overrides():
    s = load_settings(BASE | {
        "ARCHIVE_DIR": "/srv/a", "FIRMWARE_DIR": "/srv/f", "TZ_ARCHIVE": "UTC",
        "MAX_UPLOAD_BYTES": "2048", "TRASH_RETENTION_DAYS": "7",
        "ALLOW_UNAUTHENTICATED_LAN": "true", "DEVICE_HOSTNAME": "Ingest.Example.org",
    })
    assert s.archive_dir == Path("/srv/a")
    assert s.firmware_dir == Path("/srv/f")
    assert s.tz_archive == ZoneInfo("UTC")
    assert s.max_upload_bytes == 2048
    assert s.trash_retention_days == 7
    assert s.allow_unauthenticated_lan is True
    assert s.device_hostname == "ingest.example.org"


def test_missing_database_url():
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_settings({})


@pytest.mark.parametrize("tz", ["Mars/Olympus", "", "../etc"])
def test_bad_timezone(tz):
    with pytest.raises(ConfigError, match="TZ_ARCHIVE"):
        load_settings(BASE | {"TZ_ARCHIVE": tz})


@pytest.mark.parametrize("raw", ["forse", "2"])
def test_bad_bool(raw):
    with pytest.raises(ConfigError, match="ALLOW_UNAUTHENTICATED_LAN"):
        load_settings(BASE | {"ALLOW_UNAUTHENTICATED_LAN": raw})


@pytest.mark.parametrize("key,raw", [
    ("MAX_UPLOAD_BYTES", "tanti"), ("MAX_UPLOAD_BYTES", "10"),
    ("TRASH_RETENTION_DAYS", "0"),
])
def test_bad_int(key, raw):
    with pytest.raises(ConfigError, match=key):
        load_settings(BASE | {key: raw})


def test_settings_key_is_optional_and_hidden():
    key = Fernet.generate_key().decode()
    s = load_settings(BASE | {"SETTINGS_KEY": f" {key} "})
    assert s.settings_key == key
    assert key not in repr(s)
    assert load_settings(BASE | {"SETTINGS_KEY": "   "}).settings_key is None


@pytest.mark.parametrize("raw", ["corta", "x" * 44])
def test_bad_settings_key_is_treated_as_missing(raw):
    """Final review 9 (spec §8): una SETTINGS_KEY malformata ferma solo l'elaborazione AI,
    non l'ingest: niente ConfigError, chiave assente e segnalata come non valida."""
    s = load_settings(BASE | {"SETTINGS_KEY": raw})
    assert (s.settings_key, s.settings_key_invalid) == (None, True)
    assert raw not in repr(s)
    assert load_settings(BASE).settings_key_invalid is False
