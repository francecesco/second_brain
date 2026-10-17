"""Nota leggibile `<base>.md` (spec §6) e validazione dell'arricchimento (spec §9): puro.

Il `.md` è la verità per i campi AI: frontmatter YAML con titolo automatico, riassunto,
tag, lingua, provenienza ed `edited` (i campi corretti a mano); il corpo è la trascrizione.
Il titolo manuale non sta qui ma nel sidecar `.json`, come prima dell'elaborazione AI.
Le azioni (spec azioni §3) stanno nel frontmatter come lista `actions`: testo, scadenza e
spunta; sono un campo AI modificabile, e una spunta conta come correzione a mano.
"""
import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

from .languages import LANGUAGES

EDITABLE_FIELDS = ("transcript", "summary", "tags", "actions")
# Lunghezze delle colonne del catalogo (models.py le usa): un valore più lungo scritto a mano
# nel `.md` è una nota illeggibile, non un DataError a metà rescan.
MAX_TITLE_LEN = 200  # captures.title e captures.title_auto
MAX_SUMMARY_LEN = 500
MAX_TAGS = 10  # persone e programmi nominati, piu' qualche tema (spec tag e nomi §3)
MAX_TAG_LEN = 40  # elementi di captures.tags
MAX_LANGUAGE_LEN = 8  # captures.language
MAX_PROVIDER_LEN = 32  # captures.ai_provider, captures.ai_enrich_provider, ai_providers.name
MAX_MODEL_LEN = 100  # modelli in captures e ai_providers
MAX_ACTIONS = 10       # azioni per nota (spec azioni §3)
MAX_ACTION_LEN = 200   # una riga
ACTION_ID_LEN = 8      # caratteri esadecimali
# Una scadenza dell'AI fuori da questa finestra attorno al giorno della registrazione e' un
# errore di lettura della data ("2025" per "2026"), non una scadenza: si scarta.
DUE_PAST = timedelta(days=365)
DUE_FUTURE = timedelta(days=5 * 365)
DELIMITER = "---"
YAML_WIDTH = 10_000  # niente a capo automatici dentro titolo e riassunto


class NoteError(ValueError):
    """`.md` illeggibile: frontmatter mancante, YAML non valido o campi del tipo sbagliato."""


class EnrichmentInvalid(ValueError):
    """Risposta di arricchimento senza i campi richiesti o del tipo sbagliato."""


@dataclass(frozen=True)
class Action:
    """Una cosa da fare ricavata dalla nota (spec azioni §3). `done`: istante della spunta."""
    id: str
    text: str
    due: date | None = None
    done: datetime | None = None


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
    actions: tuple[Action, ...] = ()


@dataclass(frozen=True)
class NoteMeta:
    """Identita' della registrazione nel frontmatter della nota nel vault (spec vault §5):
    scritta dal backend, mai riletta. `title` e' quello mostrato (manuale o automatico), None
    se la nota non ne ha: il titolo di default per fascia sta solo nel nome del file."""
    capture_id: str
    device_id: str
    created: datetime
    duration_s: float
    title: str | None
    url: str | None
    done: bool = False   # spuntata nell'app (spec note da fare §3): `completata`, mai riletto


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


def new_action_id() -> str:
    return secrets.token_hex(ACTION_ID_LEN // 2)


def _derived_id(position: int, text: str) -> str:
    """Id stabile per un'azione scritta a mano senza `id` (es. in Obsidian): rileggere lo
    stesso file deve dare lo stesso id, altrimenti `rescan` cambierebbe le spunte."""
    return hashlib.sha256(f"{position}:{text}".encode()).hexdigest()[:ACTION_ID_LEN]


def clean_action_text(value: object) -> str | None:
    return _one_line(str(value))[:MAX_ACTION_LEN].rstrip() or None


def parse_due(value: object) -> date | None:
    """`AAAA-MM-GG` (o una data YAML) → data; qualunque altra cosa → nessuna scadenza."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def clean_actions(actions: Iterable[Action]) -> tuple[Action, ...]:
    """Testo su una riga e tagliato, niente vuote né doppioni (stesso testo), al massimo
    MAX_ACTIONS, id unici."""
    out: list[Action] = []
    seen_text: set[str] = set()
    seen_id: set[str] = set()
    for action in actions:
        text = clean_action_text(action.text)
        if text is None or text.casefold() in seen_text:
            continue
        aid = action.id if action.id and action.id not in seen_id else new_action_id()
        out.append(replace(action, id=aid, text=text))
        seen_text.add(text.casefold())
        seen_id.add(aid)
        if len(out) == MAX_ACTIONS:
            break
    return tuple(out)


def sane_due(actions: Iterable[Action], reference: date) -> tuple[Action, ...]:
    """Toglie le scadenze fuori dalla finestra attorno al giorno della registrazione."""
    low, high = reference - DUE_PAST, reference + DUE_FUTURE
    return tuple(a if a.due is None or low <= a.due <= high else replace(a, due=None)
                 for a in actions)


def actions_to_data(actions: Iterable[Action]) -> list[dict]:
    """Forma JSON delle azioni: frontmatter del `.md` e colonna del catalogo."""
    return [{"id": a.id, "text": a.text, "due": a.due.isoformat() if a.due else None,
             "done": a.done.isoformat() if a.done else None} for a in actions]


def actions_from_data(values: object) -> tuple[Action, ...]:
    """Dal frontmatter (anche scritto a mano) o dal catalogo; NoteError se non è una lista
    di voci leggibili. Una voce può essere solo un testo."""
    if values is None:
        return ()
    if not isinstance(values, list):
        raise NoteError("actions: attesa una lista")
    actions: list[Action] = []
    for position, item in enumerate(values):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise NoteError("actions: ogni voce è un testo o una mappa")
        text = clean_action_text(item.get("text") or "")
        if text is None:
            continue
        raw_id = item.get("id")
        aid = str(raw_id).strip()[:ACTION_ID_LEN] if raw_id is not None and str(raw_id).strip() else ""
        done = item.get("done")
        try:
            done_at = _datetime(done) if done not in (None, False, "") else None
        except NoteError:
            done_at = None
        if done is True:  # `done: true` scritto a mano: fatta, senza l'istante
            done_at = datetime(1970, 1, 1, tzinfo=UTC)
        actions.append(Action(aid or _derived_id(position, text), text, parse_due(item.get("due")),
                              done_at))
    return clean_actions(actions)


def _edited(fields: Iterable[str]) -> tuple[str, ...]:
    wanted = set(fields)
    return tuple(name for name in EDITABLE_FIELDS if name in wanted)


def render_note(note: Note, tz: ZoneInfo, meta: NoteMeta | None = None) -> str:
    front: dict = {}
    if meta is not None:
        front["id"] = meta.capture_id
        front["device"] = meta.device_id
        front["created"] = meta.created.astimezone(tz).isoformat(timespec="seconds")
        front["duration"] = int(round(meta.duration_s))
        if meta.title:
            front["title"] = meta.title
            front["aliases"] = [meta.title]
        if note.title and note.title != meta.title:
            front["title_auto"] = note.title   # l'automatico resta distinto dal manuale mostrato
    elif note.title:
        front["title"] = note.title
    if note.summary:
        front["summary"] = note.summary
    if note.tags:
        front["tags"] = list(note.tags)
    if meta is not None and meta.url:
        front["url"] = meta.url
    if meta is not None:
        front["completata"] = meta.done
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
    if note.actions:
        front["actions"] = actions_to_data(note.actions)
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


def parse_note_title(text: str) -> str | None:
    """Il `title` mostrato nel frontmatter (manuale o automatico), None se manca."""
    front, _ = _split(text)
    try:
        data = yaml.safe_load(front) if front.strip() else {}
    except yaml.YAMLError:
        raise NoteError("frontmatter YAML non valido") from None
    if not isinstance(data, dict):
        raise NoteError("il frontmatter non è una mappa chiave: valore")
    title = _opt_str(data, "title")
    return clean_title(title) if title is not None else None


def merge_edits(file_note: Note, file_title: str | None, note: Note,
                shown_title: str | None) -> tuple[Note, str | None, bool]:
    """Differenze tra la nota letta dal file (modificato in Obsidian) e quella del catalogo,
    applicate come correzioni a mano. Ritorna (nota aggiornata, nuovo titolo manuale o None,
    qualcosa e' cambiato). Pura (spec vault §7)."""
    changed = False
    new_title = None
    if file_title and file_title != shown_title:
        new_title = file_title
        changed = True
    if file_note.transcript != note.transcript.strip():
        note = apply_edit(note, "transcript", file_note.transcript)
        changed = True
    if (file_note.summary or None) != (note.summary or None):
        note = apply_edit(note, "summary", file_note.summary or "")
        changed = True
    if tuple(file_note.tags) != tuple(note.tags):
        note = replace(note, tags=tuple(file_note.tags), edited=_edited((*note.edited, "tags")))
        changed = True
    if file_note.actions != note.actions:
        note = edit_actions(note, file_note.actions)
        changed = True
    return note, new_title, changed


def parse_note_id(text: str) -> str | None:
    """`id` del frontmatter (il capture_id della registrazione), None se manca."""
    front, _ = _split(text)
    try:
        data = yaml.safe_load(front) if front.strip() else {}
    except yaml.YAMLError:
        raise NoteError("frontmatter YAML non valido") from None
    if not isinstance(data, dict):
        raise NoteError("il frontmatter non è una mappa chiave: valore")
    value = data.get("id")
    return str(value).strip() if value is not None and str(value).strip() else None


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
    title = _opt_str(data, "title_auto") if data.get("title_auto") is not None else _opt_str(data, "title")
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
        actions=actions_from_data(data.get("actions")),
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
                     provider: str, model: str, at: datetime,
                     actions: tuple[Action, ...] | None = None) -> Note:
    """Risultato dell'arricchimento; riassunto, tag e azioni corretti a mano restano quelli.
    `actions=None`: il provider non le ha date, restano quelle che c'erano."""
    note = replace(
        note, title=title,
        summary=note.summary if "summary" in note.edited else summary,
        tags=note.tags if "tags" in note.edited else tags,
        enrich_provider=provider, enrich_model=model, processed_at=at)
    return note if actions is None else apply_actions(note, actions)


def apply_actions(note: Note, actions: tuple[Action, ...]) -> Note:
    """Solo le azioni nuove (fase `actions`); quelle corrette o spuntate a mano restano."""
    return note if "actions" in note.edited else replace(note, actions=clean_actions(actions))


def edit_actions(note: Note, actions: Iterable[Action]) -> Note:
    """Azioni scritte, cambiate, tolte o spuntate a mano: il campo entra in `edited`."""
    return replace(note, actions=clean_actions(actions), edited=_edited((*note.edited, "actions")))


def set_action_done(note: Note, action_id: str, at: datetime | None) -> Note:
    """Spunta (o toglie la spunta a) un'azione; KeyError se l'id non c'è."""
    if not any(a.id == action_id for a in note.actions):
        raise KeyError(action_id)
    return edit_actions(note, (replace(a, done=at) if a.id == action_id else a
                               for a in note.actions))


def set_all_actions_done(note: Note, at: datetime | None) -> Note:
    """Tutte le azioni fatte (spunta della nota intera) o tutte di nuovo da fare."""
    if not note.actions:
        return note
    return edit_actions(note, (replace(a, done=at if at is None or a.done is None else a.done)
                               for a in note.actions))


def apply_tags(note: Note, tags: tuple[str, ...]) -> Note:
    """Solo i tag nuovi (fase retag); quelli corretti a mano restano."""
    return note if "tags" in note.edited else replace(note, tags=tags)


def apply_edit(note: Note, field: str, value: str) -> Note:
    """Correzione a mano dalla UI: il campo entra in `edited` e il worker non lo tocca più."""
    if field == "transcript":
        cleaned: object = value.strip()
    elif field == "summary":
        cleaned = clean_summary(value)
    elif field == "tags":
        cleaned = parse_tags_input(value)
    elif field == "actions":
        raise ValueError("le azioni si modificano con edit_actions")
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


def validate_actions(data: object) -> tuple[Action, ...] | None:
    """Azioni della risposta di arricchimento (spec azioni §4): None se la chiave manca (il
    provider non le ha date), voci illeggibili scartate una per una, id nuovi."""
    if not isinstance(data, dict) or "actions" not in data:
        return None
    values = data.get("actions")
    if not isinstance(values, list):
        return ()
    actions = []
    for item in values:
        if isinstance(item, str):
            item = {"text": item}
        if isinstance(item, dict):
            text = clean_action_text(item.get("text") or "")
            if text:
                actions.append(Action(new_action_id(), text, parse_due(item.get("due"))))
    return clean_actions(actions)
