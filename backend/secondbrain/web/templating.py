"""Jinja: template, filtri di formato, nomi dei mesi e dei giorni, titolo di default."""
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from ..notefile import MAX_SUMMARY_LEN, MAX_TITLE_LEN

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre")
WEEKDAYS = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
WEEKDAYS_SHORT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
KIB = 1024
MIB = 1024 * 1024

# Fasce orarie (ora locale, inizio incluso) per il titolo di default delle catture senza titolo.
MATTINO_START_HOUR = 5
POMERIGGIO_START_HOUR = 12
SERA_START_HOUR = 18
NOTTE_START_HOUR = 23

ROW_POLL_S = 5  # righe in coda o in corso: htmx le ricarica ogni tanti secondi
ROW_TAG_PREVIEW = 3  # tag mostrati nell'intestazione della riga, per non affollarla
DETAIL_FIELDS = ("summary", "tags", "transcript")  # ordine nel dettaglio (spec AI §10)
FIELD_LABELS = {"summary": "Riassunto", "tags": "Tag", "transcript": "Trascrizione"}


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


def default_title(recorded_at_utc: datetime, tz: ZoneInfo) -> str:
    """Titolo mostrato quando la cattura non ne ha uno: solo visualizzazione, mai salvato."""
    local = recorded_at_utc.astimezone(tz)
    hour = local.hour
    time_str = local.strftime("%H:%M")
    if MATTINO_START_HOUR <= hour < POMERIGGIO_START_HOUR:
        band = "del mattino"
    elif POMERIGGIO_START_HOUR <= hour < SERA_START_HOUR:
        band = "del pomeriggio"
    elif SERA_START_HOUR <= hour < NOTTE_START_HOUR:
        band = "della sera"
    else:
        band = "della notte"
    return f"Nota {band}, {time_str}"


def display_title(capture, tz: ZoneInfo) -> str:
    """Titolo mostrato: manuale, altrimenti automatico, altrimenti il default per fascia."""
    return capture.title or capture.title_auto or default_title(capture.recorded_at, tz)


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
templates.env.globals["default_title"] = default_title
templates.env.globals["display_title"] = display_title
templates.env.globals["reprocess_confirm"] = reprocess_confirm
templates.env.globals["DETAIL_FIELDS"] = DETAIL_FIELDS
templates.env.globals["FIELD_LABELS"] = FIELD_LABELS
templates.env.globals["ROW_POLL_S"] = ROW_POLL_S
templates.env.globals["ROW_TAG_PREVIEW"] = ROW_TAG_PREVIEW
templates.env.globals["MAX_SUMMARY_LEN"] = MAX_SUMMARY_LEN
templates.env.globals["MAX_TITLE_LEN"] = MAX_TITLE_LEN
