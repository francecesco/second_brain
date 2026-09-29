"""Configurazione del servizio, letta solo da variabili d'ambiente (spec §5)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet

DEFAULT_MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MIN_MAX_UPLOAD_BYTES = 1024
DEFAULT_TRASH_RETENTION_DAYS = 30
MIN_TRASH_RETENTION_DAYS = 1

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Variabile d'ambiente mancante o non valida."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    archive_dir: Path
    firmware_dir: Path
    tz_archive: ZoneInfo
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    trash_retention_days: int = DEFAULT_TRASH_RETENTION_DAYS
    allow_unauthenticated_lan: bool = False
    device_hostname: str | None = None
    # Chiave Fernet per le chiavi API dei provider (spec AI §8): mai nel repr, quindi mai
    # nei log anche se qualcuno stampa le impostazioni.
    settings_key: str | None = field(default=None, repr=False)


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{key}: valore booleano non valido: {raw!r}")


def _int(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key}: non è un intero: {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{key}: deve essere almeno {minimum}, trovato {value}")
    return value


def _settings_key(env: Mapping[str, str]) -> str | None:
    raw = env.get("SETTINGS_KEY", "").strip()
    if not raw:
        return None
    try:
        Fernet(raw.encode())
    except (ValueError, TypeError):
        raise ConfigError("SETTINGS_KEY non valida: generane una con 'secondbrain gen-key'") from None
    return raw


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL", "").strip()
    if not url:
        raise ConfigError("DATABASE_URL mancante")
    tz_name = env.get("TZ_ARCHIVE", "Europe/Rome")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"TZ_ARCHIVE: fuso orario sconosciuto {tz_name!r}") from None
    return Settings(
        database_url=url,
        archive_dir=Path(env.get("ARCHIVE_DIR", "/data/archive")),
        firmware_dir=Path(env.get("FIRMWARE_DIR", "/data/firmware")),
        tz_archive=tz,
        max_upload_bytes=_int(env, "MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES,
                              MIN_MAX_UPLOAD_BYTES),
        trash_retention_days=_int(env, "TRASH_RETENTION_DAYS",
                                  DEFAULT_TRASH_RETENTION_DAYS, MIN_TRASH_RETENTION_DAYS),
        allow_unauthenticated_lan=_bool(env, "ALLOW_UNAUTHENTICATED_LAN", False),
        device_hostname=env.get("DEVICE_HOSTNAME", "").strip().lower() or None,
        settings_key=_settings_key(env),
    )
