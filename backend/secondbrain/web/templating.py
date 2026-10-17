"""Jinja: template, filtri di formato, nomi dei mesi e dei giorni, titolo di default."""
import hashlib
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from ..ai.base import redact
from ..notefile import MAX_ACTION_LEN, MAX_MODEL_LEN, MAX_SUMMARY_LEN, MAX_TITLE_LEN
from ..notes import default_title, effective_title
from ..search import MAX_QUERY_LEN
from ..textnotes import is_text_capture

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"


@lru_cache(maxsize=None)
def static_url(name: str) -> str:
    """Indirizzo di un file statico con la versione del contenuto: Cloudflare e i browser
    tengono in cache CSS e JS per ore, cosi' dopo un deploy l'indirizzo cambia e si
    riscarica. Il file si legge una volta per processo (cambia solo con un nuovo deploy)."""
    digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]
    return f"/static/{name}?v={digest}"
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre")
WEEKDAYS = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
WEEKDAYS_SHORT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
MONTHS_SHORT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")
KIB = 1024
MIB = 1024 * 1024

# Fasce orarie (ora locale, inizio incluso) per il titolo di default delle catture senza titolo.

ROW_POLL_S = 5  # righe in coda o in corso: htmx le ricarica ogni tanti secondi
ROW_TAG_PREVIEW = 3  # tag mostrati nell'intestazione della riga, per non affollarla
DETAIL_FIELDS = ("summary", "actions", "tags", "transcript")  # ordine nel dettaglio (spec AI §10, azioni §5)
FIELD_LABELS = {"summary": "Riassunto", "actions": "Azioni", "tags": "Tag", "transcript": "Trascrizione"}


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_size(size: int) -> str:
    if size < MIB:
        return f"{max(1, round(size / KIB))} KB"
    return f"{size / MIB:.1f} MB".replace(".", ",")


def weekday_label(d: date) -> str:
    """"Lunedì 28": nome del giorno maiuscolo per la prima lettera + numero del giorno."""
    return f"{WEEKDAYS[d.weekday()].capitalize()} {d.day}"


def weekday_short(d: date) -> str:
    """"lun 28": per l'albero, dove lo spazio è poco."""
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day}"


def due_label(value: str | None, today: date) -> str:
    """Scadenza breve per le righe: "oggi", "domani", altrimenti "ven 23/10" (con l'anno se
    non è quello corrente)."""
    if not value:
        return ""
    due = date.fromisoformat(value)
    if due == today:
        return "oggi"
    if due == today + timedelta(days=1):
        return "domani"
    label = f"{WEEKDAYS_SHORT[due.weekday()]} {due:%d/%m}"
    return label if due.year == today.year else f"{label}/{due:%y}"


def day_month_label(d: date) -> str:
    """"Lunedì 29 set": per i risultati della ricerca, che mescolano giorni diversi."""
    return f"{weekday_label(d)} {MONTHS_SHORT[d.month - 1]}"


def display_title(capture, tz: ZoneInfo) -> str:
    """Titolo mostrato: manuale, altrimenti automatico, altrimenti il default per fascia."""
    return effective_title(capture, tz)


def reprocess_confirm(capture) -> str:
    """Domanda prima di "Rielabora": dice quali campi corretti a mano restano invariati."""
    edited = [FIELD_LABELS[name].lower() for name in DETAIL_FIELDS if name in (capture.edited or [])]
    if not edited:
        return "Rielaborare la nota?"
    return f"Rielaborare la nota? I campi corretti a mano ({', '.join(edited)}) resteranno invariati."


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["duration"] = format_duration
templates.env.filters["size"] = format_size
templates.env.globals["MONTHS"] = MONTHS
templates.env.globals["date"] = date
templates.env.globals["weekday_label"] = weekday_label
templates.env.globals["weekday_short"] = weekday_short
templates.env.globals["day_month_label"] = day_month_label
templates.env.globals["default_title"] = default_title
templates.env.globals["display_title"] = display_title
templates.env.globals["is_text_capture"] = is_text_capture
templates.env.globals["reprocess_confirm"] = reprocess_confirm
templates.env.globals["DETAIL_FIELDS"] = DETAIL_FIELDS
templates.env.globals["static_url"] = static_url
templates.env.globals["FIELD_LABELS"] = FIELD_LABELS
templates.env.globals["due_label"] = due_label
templates.env.globals["ROW_POLL_S"] = ROW_POLL_S
templates.env.globals["ROW_TAG_PREVIEW"] = ROW_TAG_PREVIEW
templates.env.globals["MAX_SUMMARY_LEN"] = MAX_SUMMARY_LEN
templates.env.globals["MAX_TITLE_LEN"] = MAX_TITLE_LEN
templates.env.globals["MAX_ACTION_LEN"] = MAX_ACTION_LEN
templates.env.globals["MAX_QUERY_LEN"] = MAX_QUERY_LEN
templates.env.globals["MAX_MODEL_LEN"] = MAX_MODEL_LEN
# `last_error` è già passato da `redact` quando è stato scritto; ripassarlo in pagina
# costa niente e copre un errore arrivato da un'altra strada.
templates.env.filters["redact"] = redact
