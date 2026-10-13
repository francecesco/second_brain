"""Immagine "Oggi" per il device: 200×136 punti in bianco e nero, dall'istantanea di HA.

Il device disegna da sé l'intestazione (ora, batteria) e il piede; il backend disegna la
fascia centrale con lo stesso Space Grotesk della UI web. `lines` decide il contenuto,
`render` lo impagina, `pack` lo trasforma nei 3.400 byte che il device copia sul pannello.
A parità di istantanea, ora e fuso l'immagine è identica byte per byte (l'ETag ne dipende).
"""
import hashlib
import io
import math
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from . import homeassistant as ha
from .ha_store import Snapshot

WIDTH, HEIGHT = 200, 136
PACKED_BYTES = WIDTH * HEIGHT // 8      # 3400
STALE_AFTER = timedelta(minutes=30)
MAX_TODAY, MAX_TOMORROW = 3, 2
# I testi arrivano da Home Assistant e i calendari possono essere condivisi da fuori: tetti
# su lunghezze e quantità prima di misurare o ordinare, perché render() resti rapido.
MAX_TEXT_CHARS = 200
MAX_DATE_CHARS = 40
MAX_EVENTS, MAX_SENSORS = 500, 20

FONT_PATH = Path(__file__).parent / "assets" / "SpaceGrotesk-Bold.ttf"
MARGIN_X = 4
MAX_TEXT_W = WIDTH - 2 * MARGIN_X       # 192: oltre si taglia con "…"
ELLIPSIS = "…"
SENSOR_SEP = " · "
# stile → (dimensione del font in px, altezza della riga, distanza della linea di base dal
# bordo alto della riga). Scelte guardando le anteprime: a 11 px il grassetto esce con tratti
# da 1 punto e sembra un light; 15 px ha le stesse maiuscole del 16 (11 punti) e con righe da
# 15 OGGI 3 + DOMANI 2 + SPESA + una riga di sensori stanno nei 136 punti (135). La linea
# di base dei titoli a 9 lascia la prima fila bianca: le maiuscole (8 punti) non toccano il
# bordo alto né le discendenti della riga sopra.
STYLES = {
    "head": (12, 10, 9),
    "item": (15, 15, 12),
    "small": (12, 12, 10),
}
NO_SPACE_UNITS = ("°C", "°F", "°", "%")


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # BASIC: stessi byte con o senza raqm installato (l'ETag dipende dall'immagine)
    return ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)


def _clean(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Testo su una riga, spazi compattati, al massimo `limit` caratteri."""
    return " ".join(str(text)[:limit * 4].split())[:limit]


# --- contenuto ---------------------------------------------------------------------------

def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:MAX_DATE_CHARS][:10])
    except ValueError:
        return None


def _datetime(value: str, tz: ZoneInfo) -> datetime | None:
    try:
        when = datetime.fromisoformat(value[:MAX_DATE_CHARS])
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=tz)).astimezone(tz)


def _day_events(events, day: date, now: datetime, tz: ZoneInfo, *, today: bool,
                limit: int) -> list[str]:
    """Eventi del giorno `day`: tutto il giorno prima, poi per ora di inizio.

    Tutto il giorno: inizio ≤ giorno < fine (fine esclusiva, come la manda HA). A orario,
    oggi: non ancora finito e iniziato prima di domani (compresi quelli in corso o a
    cavallo della mezzanotte); quelli iniziati prima di oggi e ancora in corso si mostrano
    senza ora, come quelli di tutto il giorno. Domani: solo quelli che iniziano domani (anche
    di tutto il giorno), per non ripetere quelli già in "OGGI".
    """
    day_start = datetime.combine(day, time(), tz)
    day_end = datetime.combine(day + timedelta(days=1), time(), tz)
    found: list[tuple] = []
    for event in events[:MAX_EVENTS]:
        summary, calendar = _clean(event.summary), _clean(event.calendar)
        if event.all_day:
            start = _date(event.start)
            end = _date(event.end) or (start + timedelta(days=1) if start else None)
            if start and start <= day < end and (today or start == day):
                found.append((0, day_start, summary, calendar, summary))
            continue
        start, end = _datetime(event.start, tz), _datetime(event.end, tz)
        if start is None:
            continue
        end = end or start
        if today:
            keep = end > now and start < day_end
        else:
            keep = day_start <= start < day_end
        if keep and start < day_start:
            found.append((0, day_start, summary, calendar, summary))
        elif keep:
            found.append((1, start, summary, calendar, f"{start:%H:%M} {summary}"))
    found.sort(key=lambda f: f[:4])
    return [f[4] for f in found[:limit]]


def _sensor_value(sensor: ha.SensorValue) -> str:
    state = _clean(sensor.state, 40)
    try:
        number = float(state)
    except ValueError:
        return state
    if not math.isfinite(number):
        return state
    text = state if state.lstrip("-").isdigit() else f"{number:.1f}"
    text = text.replace(".", ",")
    unit = _clean(sensor.unit or "", 20)
    if not unit:
        return text
    return text + (unit if unit in NO_SPACE_UNITS else f" {unit}")


def lines(snapshot: Snapshot, now: datetime, tz: ZoneInfo, *,
          shopping: bool) -> list[tuple[str, str]]:
    """Righe (stile, testo) prima dell'impaginazione; stile in head, item, small.

    `shopping` dice se nella configurazione c'è una lista scelta: senza, niente "SPESA".
    `now` deve avere il fuso (aware), altrimenti ValueError.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("digest: serve un `now` con il fuso orario")
    reading = snapshot.reading
    events = reading.events if reading else ()
    today = now.astimezone(tz).date()
    rows: list[tuple[str, str]] = [("head", "OGGI")]
    today_items = _day_events(events, today, now, tz, today=True, limit=MAX_TODAY)
    rows += [("item", t) for t in today_items] or [("item", "niente in programma")]

    tomorrow_items = _day_events(events, today + timedelta(days=1), now, tz, today=False,
                                 limit=MAX_TOMORROW)
    if tomorrow_items:
        rows += [("head", "DOMANI")] + [("item", t) for t in tomorrow_items]

    if shopping:
        count = len(reading.shopping) if reading else 0
        text = "vuota" if count == 0 else "1 articolo" if count == 1 else f"{count} articoli"
        rows += [("head", "SPESA"), ("item", text)]

    sensors = (reading.sensors if reading else ())[:MAX_SENSORS]
    values = [f"{_clean(s.label, 40)} {_sensor_value(s)}" for s in sensors]
    rows += [("item", SENSOR_SEP.join(values[i:i + 2])) for i in range(0, len(values), 2)]

    if snapshot.fetched_at is not None and now - snapshot.fetched_at > STALE_AFTER:
        rows.append(("small", f"agg. {snapshot.fetched_at.astimezone(tz):%H:%M}"))
    return rows


# --- impaginazione -----------------------------------------------------------------------

def _fit(text: str, font: ImageFont.FreeTypeFont) -> str:
    """Il testo, o il suo inizio più "…", entro `MAX_TEXT_W`."""
    text = _clean(text)
    if font.getlength(text) <= MAX_TEXT_W:
        return text
    low, high = 0, len(text)        # il prefisso più lungo che sta con "…" (ricerca binaria)
    while low < high:
        mid = (low + high + 1) // 2
        if font.getlength(text[:mid].rstrip() + ELLIPSIS) <= MAX_TEXT_W:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + ELLIPSIS


def render(snapshot: Snapshot, now: datetime, tz: ZoneInfo, *, shopping: bool) -> Image.Image:
    """Immagine modo "1" 200×136. Le righe che non stanno si tolgono dal fondo del
    contenuto; la riga "agg." resta sempre in basso. `now` deve avere il fuso."""
    rows = lines(snapshot, now, tz, shopping=shopping)
    small = [r for r in rows if r[0] == "small"]
    body = [r for r in rows if r[0] != "small"]
    # se la prima riga sporgerebbe sopra y = 0 (es. maiuscola accentata), si scende quanto basta
    top = 0
    if body:
        size, _, baseline = STYLES[body[0][0]]
        font = _font(size)
        top = max(0, 1 - (baseline + font.getbbox(_fit(body[0][1], font), anchor="ls")[1]))
    room = HEIGHT - top - sum(STYLES[s][1] for s, _ in small)
    while body and sum(STYLES[s][1] for s, _ in body) > room:
        body.pop()
    while body and body[-1][0] == "head":       # niente titolo senza righe sotto
        body.pop()

    image = Image.new("1", (WIDTH, HEIGHT), 1)
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"
    y = top
    for style, text in body:
        size, height, baseline = STYLES[style]
        font = _font(size)
        draw.text((MARGIN_X, y + baseline), _fit(text, font), font=font, fill=0, anchor="ls")
        y += height
    y = HEIGHT
    for style, text in reversed(small):
        size, height, baseline = STYLES[style]
        y -= height
        font = _font(size)
        draw.text((MARGIN_X, y + baseline), _fit(text, font), font=font, fill=0, anchor="ls")
    return image


def pack(image: Image.Image) -> bytes:
    """Bit 1 = nero, MSB first, righe di 25 byte (il contrario del modo "1" di Pillow)."""
    raw = image.convert("1").tobytes()
    return bytes(b ^ 0xFF for b in raw)


def etag(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:16] + '"'


def preview_png(image: Image.Image, scale: int = 2) -> bytes:
    """PNG ingrandito senza sfumature, per guardare l'immagine sul computer."""
    big = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    out = io.BytesIO()
    big.save(out, format="PNG")
    return out.getvalue()
