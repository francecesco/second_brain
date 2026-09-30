"""Nota leggibile `<base>.md` (spec §6) e validazione dell'arricchimento (spec §9): puro.

Il `.md` è la verità per i campi AI: frontmatter YAML con titolo automatico, riassunto,
tag, lingua, provenienza ed `edited` (i campi corretti a mano); il corpo è la trascrizione.
Il titolo manuale non sta qui ma nel sidecar `.json`, come prima dell'elaborazione AI.
"""
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import yaml

from .languages import LANGUAGES

EDITABLE_FIELDS = ("transcript", "summary", "tags")
# Lunghezze delle colonne del catalogo (models.py le usa): un valore più lungo scritto a mano
# nel `.md` è una nota illeggibile, non un DataError a metà rescan.
MAX_TITLE_LEN = 200  # captures.title e captures.title_auto
MAX_SUMMARY_LEN = 500
MAX_TAGS = 8
MAX_TAG_LEN = 40  # elementi di captures.tags
MAX_LANGUAGE_LEN = 8  # captures.language
MAX_PROVIDER_LEN = 32  # captures.ai_provider, captures.ai_enrich_provider, ai_providers.name
MAX_MODEL_LEN = 100  # modelli in captures e ai_providers
DELIMITER = "---"
YAML_WIDTH = 10_000  # niente a capo automatici dentro titolo e riassunto


class NoteError(ValueError):
    """`.md` illeggibile: frontmatter mancante, YAML non valido o campi del tipo sbagliato."""


class EnrichmentInvalid(ValueError):
    """Risposta di arricchimento senza i campi richiesti o del tipo sbagliato."""


@dataclass(frozen=True)
class Note:
    transcript: str = ""
    title: str | None = None  # titolo automatico
    summary: str | None = None
    tags: tuple[str, ...] = ()
    language: str | None = None
    provider: str | None = None  # chi ha trascritto (e quindi ha sentito l'audio)
    transcribe_model: str | None = None
    enrich_provider: str | None = None
    enrich_model: str | None = None
    processed_at: datetime | None = None
    edited: tuple[str, ...] = ()


def _one_line(value: str) -> str:
    return " ".join(value.split())


def clean_title(value: str) -> str | None:
    return _one_line(value)[:MAX_TITLE_LEN].rstrip() or None


def clean_summary(value: str) -> str | None:
    return _one_line(value)[:MAX_SUMMARY_LEN].rstrip() or None


def normalize_tags(values: Iterable[object]) -> tuple[str, ...]:
    """Minuscoli, senza `#` e spazi ai bordi, senza doppioni, al massimo MAX_TAGS."""
    tags: list[str] = []
    for raw in values:
        tag = _one_line(str(raw)).lstrip("#").strip().lower()[:MAX_TAG_LEN].rstrip()
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) == MAX_TAGS:
            break
    return tuple(tags)


def parse_tags_input(text: str) -> tuple[str, ...]:
    """Tag scritti a mano nella UI: separati da virgole."""
    return normalize_tags(text.split(","))


def _edited(fields: Iterable[str]) -> tuple[str, ...]:
    wanted = set(fields)
    return tuple(name for name in EDITABLE_FIELDS if name in wanted)


def render_note(note: Note, tz: ZoneInfo) -> str:
    front: dict = {}
    if note.title:
        front["title"] = note.title
    if note.summary:
        front["summary"] = note.summary
    if note.tags:
        front["tags"] = list(note.tags)
    if note.language:
        front["language"] = note.language
    if note.provider:
        front["provider"] = note.provider
    if note.enrich_provider and note.enrich_provider != note.provider:
        front["enrich_provider"] = note.enrich_provider
    models = {key: value for key, value in (("transcribe", note.transcribe_model),
                                            ("enrich", note.enrich_model)) if value}
    if models:
        front["models"] = models
    if note.processed_at:
        front["processed_at"] = note.processed_at.astimezone(tz).isoformat(timespec="seconds")
    front["edited"] = list(note.edited)
    text = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=None,
                          width=YAML_WIDTH)
    head = f"{DELIMITER}\n{text}{DELIMITER}\n"
    body = note.transcript.strip()
    return f"{head}\n{body}\n" if body else head


def _split(text: str) -> tuple[str, str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0].strip() != DELIMITER:
        raise NoteError("frontmatter mancante")
    for i in range(1, len(lines)):
        if lines[i].strip() == DELIMITER:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise NoteError("frontmatter non chiuso")


def _opt_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise NoteError(f"{key}: atteso un testo")
    return str(value)


def _bounded(data: dict, key: str, max_len: int) -> str | None:
    value = _opt_str(data, key)
    if value is not None and len(value) > max_len:
        raise NoteError(f"{key}: più lungo di {max_len} caratteri")
    return value


def _language(data: dict) -> str | None:
    value = _opt_str(data, "language")
    if value is not None and value not in LANGUAGES:
        raise NoteError(f"language: lingua sconosciuta {value[:MAX_LANGUAGE_LEN]!r}")
    return value


def _list(data: dict, key: str) -> list:
    value = data.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]  # `tags: lavoro` scritto a mano in Obsidian
    if not isinstance(value, list):
        raise NoteError(f"{key}: attesa una lista")
    return value


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise NoteError("processed_at: data non valida") from None
    else:
        raise NoteError("processed_at: data non valida")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_note(text: str) -> Note:
    front, body = _split(text)
    try:
        data = yaml.safe_load(front) if front.strip() else {}
    except yaml.YAMLError:
        raise NoteError("frontmatter YAML non valido") from None
    if not isinstance(data, dict):
        raise NoteError("il frontmatter non è una mappa chiave: valore")
    models = data.get("models") or {}
    if not isinstance(models, dict):
        raise NoteError("models: attesa una mappa")
    title = _opt_str(data, "title")
    summary = _opt_str(data, "summary")
    provider = _bounded(data, "provider", MAX_PROVIDER_LEN)
    enrich_model = _bounded(models, "enrich", MAX_MODEL_LEN)
    return Note(
        transcript=body.strip(),
        title=clean_title(title) if title is not None else None,
        summary=clean_summary(summary) if summary is not None else None,
        tags=normalize_tags(_list(data, "tags")),
        language=_language(data),
        provider=provider,
        transcribe_model=_bounded(models, "transcribe", MAX_MODEL_LEN),
        enrich_provider=(_bounded(data, "enrich_provider", MAX_PROVIDER_LEN)
                         or (provider if enrich_model else None)),
        enrich_model=enrich_model,
        processed_at=_datetime(data.get("processed_at")),
        edited=_edited(str(name) for name in _list(data, "edited")),
    )


def apply_transcript(note: Note, text: str, *, provider: str, model: str, language: str,
                     at: datetime) -> Note:
    """Risultato della trascrizione; una trascrizione corretta a mano non si tocca.

    Senza parlato non ci sarà arricchimento: titolo, riassunto e tag di un'elaborazione
    precedente ("Rielabora") non descrivono più niente e si tolgono, salvo quelli corretti
    a mano.
    """
    keep = "transcript" in note.edited
    transcript = note.transcript if keep else text.strip()
    note = replace(note, transcript=transcript, provider=provider, transcribe_model=model,
                   language=language, processed_at=at)
    if transcript:
        return note
    return replace(note, title=None, enrich_provider=None, enrich_model=None,
                   summary=note.summary if "summary" in note.edited else None,
                   tags=note.tags if "tags" in note.edited else ())


def apply_enrichment(note: Note, title: str, summary: str | None, tags: tuple[str, ...], *,
                     provider: str, model: str, at: datetime) -> Note:
    """Risultato dell'arricchimento; riassunto e tag corretti a mano restano quelli."""
    return replace(
        note, title=title,
        summary=note.summary if "summary" in note.edited else summary,
        tags=note.tags if "tags" in note.edited else tags,
        enrich_provider=provider, enrich_model=model, processed_at=at)


def apply_edit(note: Note, field: str, value: str) -> Note:
    """Correzione a mano dalla UI: il campo entra in `edited` e il worker non lo tocca più."""
    if field == "transcript":
        cleaned: object = value.strip()
    elif field == "summary":
        cleaned = clean_summary(value)
    elif field == "tags":
        cleaned = parse_tags_input(value)
    else:
        raise ValueError(f"campo non modificabile: {field!r}")
    return replace(note, **{field: cleaned}, edited=_edited((*note.edited, field)))


def validate_enrichment(data: object) -> tuple[str, str | None, tuple[str, ...]]:
    """(titolo, riassunto, tag) normalizzati, o EnrichmentInvalid (spec §9)."""
    if not isinstance(data, dict):
        raise EnrichmentInvalid("la risposta non è un oggetto JSON")
    title, summary, tags = data.get("title"), data.get("summary"), data.get("tags")
    if not isinstance(title, str) or clean_title(title) is None:
        raise EnrichmentInvalid("titolo mancante")
    if not isinstance(summary, str):
        raise EnrichmentInvalid("riassunto mancante")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise EnrichmentInvalid("tag mancanti o non testuali")
    return clean_title(title), clean_summary(summary), normalize_tags(tags)
