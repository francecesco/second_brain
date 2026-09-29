"""Lingue delle note: nome per i prompt e configurazione della ricerca full-text (spec §8, §10)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str        # nome in italiano, usato nei prompt ai modelli
    ts_config: str   # configurazione di testo di Postgres per stemming e stop word


LANGUAGES = {lang.code: lang for lang in (
    Language("it", "italiano", "italian"),
    Language("en", "inglese", "english"),
    Language("fr", "francese", "french"),
    Language("de", "tedesco", "german"),
    Language("es", "spagnolo", "spanish"),
    Language("pt", "portoghese", "portuguese"),
)}
DEFAULT_LANGUAGE = "it"
FALLBACK_TS_CONFIG = "simple"  # lingua sconosciuta: niente stemming, ma la ricerca funziona


def language_name(code: str) -> str:
    lang = LANGUAGES.get(code)
    return lang.name if lang else code


def text_search_config(code: str | None) -> str:
    """Configurazione di Postgres per la lingua della nota; senza lingua, quella di default."""
    lang = LANGUAGES.get(code or DEFAULT_LANGUAGE)
    return lang.ts_config if lang else FALLBACK_TS_CONFIG
