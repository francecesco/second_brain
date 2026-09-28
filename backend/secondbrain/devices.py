"""Dispositivi: token, autenticazione, metadati dagli header (spec §5, §6 punto 1)."""
import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Device
from .naming import DEFAULT_DEVICE_TYPE, is_valid_device_id, is_valid_device_type

TOKEN_BYTES = 32
POWER_SOURCES = ("battery", "usb")
MAX_FIRMWARE_LEN = 32
MAX_NAME_LEN = 100
MAX_BATTERY_V = 10.0


class DeviceError(ValueError):
    """Operazione non valida su un dispositivo."""


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    # Il token è casuale a 256 bit: basta un hash veloce, non serve un KDF.
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(headers: Mapping[str, str]) -> str | None:
    scheme, _, value = headers.get("authorization", "").strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def create_device(s: Session, device_id: str, name: str, type_: str,
                  now: datetime) -> tuple[Device, str]:
    if not is_valid_device_id(device_id):
        raise DeviceError(f"id non valido: {device_id!r} (il MAC in 12 cifre esadecimali minuscole)")
    if not is_valid_device_type(type_):
        raise DeviceError(f"tipo non valido: {type_!r}")
    if s.get(Device, device_id) is not None:
        raise DeviceError(f"dispositivo {device_id} già registrato: usa 'device token' per un nuovo token")
    token = generate_token()
    device = Device(id=device_id, name=name.strip()[:MAX_NAME_LEN] or device_id, type=type_,
                    token_hash=hash_token(token), created_at=now)
    s.add(device)
    s.flush()
    return device, token


def _require(s: Session, device_id: str) -> Device:
    device = s.get(Device, device_id)
    if device is None:
        raise DeviceError(f"dispositivo {device_id} sconosciuto")
    return device


def regenerate_token(s: Session, device_id: str) -> str:
    device = _require(s, device_id)
    token = generate_token()
    device.token_hash = hash_token(token)
    s.flush()
    return token


def rename_device(s: Session, device_id: str, name: str) -> Device:
    device = _require(s, device_id)
    clean = name.strip()[:MAX_NAME_LEN]
    if not clean:
        raise DeviceError("il nome non può essere vuoto")
    device.name = clean
    s.flush()
    return device


def _by_token(s: Session, token: str) -> Device | None:
    return s.scalar(select(Device).where(Device.token_hash == hash_token(token)))


def authenticate(s: Session, token: str | None, device_id: str, allow_unauth: bool,
                 now: datetime) -> Device:
    """Dispositivo autorizzato a caricare come `device_id`, oppure AuthError."""
    if token:
        device = _by_token(s, token)
        if device is None:
            raise AuthError(401, "token non valido")
        if device.id != device_id:
            raise AuthError(403, "X-Device-Id non corrisponde al token")
        return device
    if not allow_unauth:
        raise AuthError(401, "token mancante")
    if not is_valid_device_id(device_id):
        raise AuthError(400, "X-Device-Id mancante o non valido")
    device = s.get(Device, device_id)
    if device is None:
        device = Device(id=device_id, name=device_id, type=DEFAULT_DEVICE_TYPE,
                        token_hash=None, created_at=now)
        s.add(device)
        s.flush()
    return device


def authenticate_any(s: Session, token: str | None, allow_unauth: bool) -> Device | None:
    """Per le richieste senza X-Device-Id (firmware OTA)."""
    if token:
        device = _by_token(s, token)
        if device is None:
            raise AuthError(401, "token non valido")
        return device
    if allow_unauth:
        return None
    raise AuthError(401, "token mancante")


def _pct(raw: str | None) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def _volts(raw: str | None) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if 0.0 < value < MAX_BATTERY_V else None  # NaN non passa


def _source(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    return value if value in POWER_SOURCES else None


@dataclass(frozen=True)
class DeviceMeta:
    firmware_version: str | None = None
    battery_pct: int | None = None
    battery_v: float | None = None
    power_source: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "DeviceMeta":
        firmware = (headers.get("x-firmware-version") or "").strip()[:MAX_FIRMWARE_LEN]
        return cls(
            firmware_version=firmware or None,
            battery_pct=_pct(headers.get("x-battery-pct")),
            battery_v=_volts(headers.get("x-battery-voltage")),
            power_source=_source(headers.get("x-power-source")),
        )


def record_seen(device: Device, meta: DeviceMeta, now: datetime) -> None:
    device.last_seen_at = now
    device.last_firmware = meta.firmware_version
    device.last_battery_pct = meta.battery_pct
    device.last_battery_v = meta.battery_v
    device.last_power_source = meta.power_source
