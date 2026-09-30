"""Impostazioni dell'elaborazione e chiavi API cifrate in Postgres (spec §8).

Le chiavi si cifrano con Fernet usando SETTINGS_KEY, che sta solo nel `.env`: un backup
del database da solo non le rivela. Senza SETTINGS_KEY non si salvano chiavi e il
worker resta fermo; tutto il resto del backend funziona.
"""
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .ai.registry import PROVIDER_SPECS, PROVIDERS, ProviderConfig
from .languages import DEFAULT_LANGUAGE, LANGUAGES
from .models import AiProvider, Setting
from .notefile import MAX_MODEL_LEN

PAUSED_KEY = "processing_paused"
LANGUAGE_KEY = "language"
MASK_PREFIX = 4
MASK_SUFFIX = 3
MIN_MASKABLE_LEN = 12  # sotto, anche 7 caratteri su pochi sarebbero troppi da mostrare
MASK_HIDDEN = "…"
MOVE_UP = "up"
MOVE_DOWN = "down"
NO_KEY = "nessuna chiave"
UNREADABLE_KEY = "chiave illeggibile con la SETTINGS_KEY attuale: reinseriscila"


class NoSettingsKey(RuntimeError):
    """SETTINGS_KEY non impostata: le chiavi API non si possono cifrare."""


class UnknownProvider(LookupError):
    """Nome di provider che il backend non conosce."""


class SecretBox:
    """`invalid`: nel `.env` c'era una SETTINGS_KEY, ma malformata (config la scarta): per il
    resto è come se mancasse, ma gli avvisi dicono di correggerla invece che di crearla."""

    def __init__(self, key: str | None, *, invalid: bool = False):
        self._fernet = Fernet(key.encode()) if key else None
        self.invalid = invalid and self._fernet is None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plain: str) -> str:
        if self._fernet is None:
            raise NoSettingsKey("SETTINGS_KEY non impostata")
        return self._fernet.encrypt(plain.encode()).decode("ascii")

    def decrypt(self, token: str) -> str | None:
        """La chiave in chiaro, o None se manca SETTINGS_KEY o il token è di un'altra chiave."""
        if self._fernet is None:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def mask_key(plain: str) -> str:
    if len(plain) < MIN_MASKABLE_LEN:
        return MASK_HIDDEN
    return f"{plain[:MASK_PREFIX]}{MASK_HIDDEN}{plain[-MASK_SUFFIX:]}"


def get_setting(s: Session, key: str, default: Any) -> Any:
    row = s.get(Setting, key)
    return default if row is None else row.value


def set_setting(s: Session, key: str, value: Any) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value
    s.flush()


def is_paused(s: Session) -> bool:
    return bool(get_setting(s, PAUSED_KEY, False))


def set_paused(s: Session, paused: bool) -> None:
    set_setting(s, PAUSED_KEY, paused)


def get_language(s: Session) -> str:
    code = get_setting(s, LANGUAGE_KEY, DEFAULT_LANGUAGE)
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def set_language(s: Session, code: str) -> None:
    if code not in LANGUAGES:
        raise ValueError(f"lingua non supportata: {code!r}")
    set_setting(s, LANGUAGE_KEY, code)


def list_providers(s: Session) -> list[AiProvider]:
    stmt = (select(AiProvider).where(AiProvider.name.in_(PROVIDER_SPECS))
            .order_by(AiProvider.position, AiProvider.name))
    return list(s.scalars(stmt))


def ensure_providers(s: Session, now: datetime) -> list[AiProvider]:
    """Una riga per ogni provider conosciuto, con i default dei modelli; ordine = PROVIDERS."""
    rows = {row.name: row for row in s.scalars(select(AiProvider))}
    position = max((row.position for row in rows.values()), default=0)
    for spec in PROVIDERS:
        if spec.name not in rows:
            position += 1
            s.add(AiProvider(name=spec.name, enabled=True, position=position, api_key_enc=None,
                             transcribe_model=spec.transcribe_model, text_model=spec.text_model,
                             updated_at=now))
    s.flush()
    return list_providers(s)


def _row(s: Session, name: str) -> AiProvider:
    if name not in PROVIDER_SPECS:
        raise UnknownProvider(name)
    row = s.get(AiProvider, name)
    if row is None:
        raise UnknownProvider(name)
    return row


def save_provider(s: Session, box: SecretBox, name: str, *, api_key: str, transcribe_model: str,
                  text_model: str, enabled: bool, now: datetime) -> AiProvider:
    """`api_key` vuota = chiave invariata (il campo nel browser è in sola scrittura)."""
    row = _row(s, name)
    if api_key.strip():
        row.api_key_enc = box.encrypt(api_key.strip())
    row.transcribe_model = transcribe_model.strip()[:MAX_MODEL_LEN]
    row.text_model = text_model.strip()[:MAX_MODEL_LEN]
    row.enabled = enabled
    row.updated_at = now
    s.flush()
    return row


def clear_api_key(s: Session, name: str, now: datetime) -> None:
    row = _row(s, name)
    row.api_key_enc = None
    row.updated_at = now
    s.flush()


def move_provider(s: Session, name: str, direction: str, now: datetime) -> None:
    rows = ensure_providers(s, now)
    names = [row.name for row in rows]
    if name not in names:
        raise UnknownProvider(name)
    index = names.index(name)
    other = index - 1 if direction == MOVE_UP else index + 1
    if not 0 <= other < len(rows):
        return
    rows[index].position, rows[other].position = rows[other].position, rows[index].position
    rows[index].updated_at = rows[other].updated_at = now
    s.flush()


def provider_configs(s: Session, box: SecretBox) -> list[ProviderConfig]:
    """Principale e riserve in ordine, saltando quelli disabilitati o senza chiave leggibile."""
    configs = []
    for row in list_providers(s):
        if not row.enabled or row.api_key_enc is None:
            continue
        key = box.decrypt(row.api_key_enc)
        if key is None:
            continue
        configs.append(ProviderConfig(name=row.name, transcribe_model=row.transcribe_model,
                                      text_model=row.text_model, api_key=key))
    return configs


def has_configured_provider(s: Session) -> bool:
    """Senza decifrare: c'è almeno un provider abilitato con una chiave salvata?"""
    return bool(s.scalar(select(exists().where(AiProvider.enabled.is_(True),
                                               AiProvider.api_key_enc.is_not(None)))))


def key_status(row: AiProvider, box: SecretBox) -> str:
    """Cosa mostrare al posto della chiave: mai la chiave in chiaro."""
    if row.api_key_enc is None:
        return NO_KEY
    plain = box.decrypt(row.api_key_enc)
    return UNREADABLE_KEY if plain is None else mask_key(plain)
