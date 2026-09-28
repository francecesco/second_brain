import pytest

from secondbrain.devices import (AuthError, DeviceError, DeviceMeta, authenticate,
                                 authenticate_any, bearer_token, create_device,
                                 generate_token, hash_token, record_seen,
                                 regenerate_token, rename_device)
from secondbrain.models import Device
from tests.helpers import DEV, DEV2, NOW


def test_token_and_hash():
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 40
    assert hash_token(a) == hash_token(a)
    assert len(hash_token(a)) == 64


@pytest.mark.parametrize("headers,expected", [
    ({"authorization": "Bearer abc"}, "abc"),
    ({"authorization": "bearer  abc "}, "abc"),
    ({"authorization": "Basic abc"}, None),
    ({"authorization": "Bearer "}, None),
    ({}, None),
])
def test_bearer_token(headers, expected):
    assert bearer_token(headers) == expected


def test_create_device_stores_only_the_hash(db):
    dev, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    db.commit()
    assert dev.token_hash == hash_token(token)
    assert db.get(Device, DEV).name == "e-paper"


def test_create_device_twice_fails(db):
    create_device(db, DEV, "e-paper", "epaper154", NOW)
    with pytest.raises(DeviceError, match="già registrato"):
        create_device(db, DEV, "altro", "epaper154", NOW)


@pytest.mark.parametrize("device_id,type_", [("70:04:1d:d8:26:3c", "epaper154"), (DEV, "E Paper")])
def test_create_device_validates(db, device_id, type_):
    with pytest.raises(DeviceError):
        create_device(db, device_id, "x", type_, NOW)


def test_authenticate_with_token(db):
    dev, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    assert authenticate(db, token, DEV, False, NOW) is dev


def test_authenticate_wrong_token(db):
    create_device(db, DEV, "e-paper", "epaper154", NOW)
    with pytest.raises(AuthError) as err:
        authenticate(db, "sbagliato", DEV, False, NOW)
    assert err.value.status == 401


def test_token_of_another_device_is_forbidden(db):
    _, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    with pytest.raises(AuthError) as err:
        authenticate(db, token, DEV2, False, NOW)
    assert err.value.status == 403


def test_missing_token_rejected(db):
    with pytest.raises(AuthError) as err:
        authenticate(db, None, DEV, False, NOW)
    assert err.value.status == 401


def test_missing_token_allowed_on_lan_registers_device(db):
    dev = authenticate(db, None, DEV, True, NOW)
    db.commit()
    assert (dev.type, dev.token_hash, dev.name) == ("epaper154", None, DEV)
    assert db.get(Device, DEV) is not None


def test_lan_mode_still_validates_device_id(db):
    with pytest.raises(AuthError) as err:
        authenticate(db, None, "../x", True, NOW)
    assert err.value.status == 400


def test_regenerate_token_invalidates_old_one(db):
    _, old = create_device(db, DEV, "e-paper", "epaper154", NOW)
    new = regenerate_token(db, DEV)
    assert authenticate(db, new, DEV, False, NOW).id == DEV
    with pytest.raises(AuthError):
        authenticate(db, old, DEV, False, NOW)
    with pytest.raises(DeviceError):
        regenerate_token(db, DEV2)


def test_authenticate_any(db):
    dev, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    assert authenticate_any(db, token, False) is dev
    assert authenticate_any(db, None, True) is None
    for token_, allow in [(None, False), ("sbagliato", True)]:
        with pytest.raises(AuthError) as err:
            authenticate_any(db, token_, allow)
        assert err.value.status == 401


def test_device_meta_from_headers():
    meta = DeviceMeta.from_headers({
        "x-firmware-version": "0.6.2", "x-battery-pct": "95",
        "x-battery-voltage": "4.15", "x-power-source": "Battery",
    })
    assert meta == DeviceMeta("0.6.2", 95, 4.15, "battery")
    junk = DeviceMeta.from_headers({
        "x-battery-pct": "abc", "x-battery-voltage": "nan", "x-power-source": "solar",
    })
    assert junk == DeviceMeta()
    assert DeviceMeta.from_headers({"x-battery-pct": "150"}).battery_pct is None


def test_record_seen_and_rename(db):
    dev, _ = create_device(db, DEV, "e-paper", "epaper154", NOW)
    record_seen(dev, DeviceMeta("0.6.2", None, 4.9, "usb"), NOW)
    assert (dev.last_seen_at, dev.last_firmware, dev.last_power_source) == (NOW, "0.6.2", "usb")
    assert rename_device(db, DEV, "  scrivania  ").name == "scrivania"
    with pytest.raises(DeviceError):
        rename_device(db, DEV, "   ")
    with pytest.raises(DeviceError):
        rename_device(db, DEV2, "x")
