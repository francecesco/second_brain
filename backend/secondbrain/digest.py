"""Immagine "Oggi" per il device: 200×136 punti in bianco e nero, dall'istantanea di HA.

Solo la giornata di oggi (eventi non ancora finiti, senza titolo: "oggi" è implicito) e i
sensori, divisi da un filetto; la spesa non è nell'immagine. In caratteri grandi: sul pannello da 1,54" quelli da 12-15 px non si leggevano.

Il device disegna da sé l'intestazione (ora, batteria) e il piede; il backend disegna la
fascia centrale con lo stesso Space Grotesk della UI web. `lines` decide il contenuto,
`render` lo impagina, `pack` lo trasforma nei 3.400 byte che il device copia sul pannello.
A parità di istantanea, ora e fuso l'immagine è identica byte per byte (l'ETag ne dipende).
"""
import hashlib
import io
import math
import struct
import unicodedata
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
MAX_TODAY = 4
TOP_GAP = 3                             # la fascia nera del device sta sopra: un po' d'aria
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
# bordo alto della riga). Scelte guardando le anteprime composte nella schermata del device:
# sotto i 20 px il grassetto esce con tratti da 1 punto e sembra un light (19 è già più
# chiaro), a 20 le aste sono da 2 punti come l'ora dell'intestazione. Con righe da 20,
# 4 eventi, il filetto e due sensori stanno nei 136 punti (132 con l'aria in alto). La linea
# di base delle righe a 15 tiene le discendenti (4 punti) dentro la riga. "rule" è il
# filetto: 1 punto di nero con 4 di bianco sopra e 4 sotto (testo vuoto, dimensione 0).
STYLES = {
    "rule": (0, 9, 4),
    "item": (20, 20, 15),
    "small": (14, 14, 11),
}
EMPTY_DAY = "nulla in programma"        # "niente in programma" a 20 px non sta in 192 punti
NO_SPACE_UNITS = ("°C", "°F", "°", "%")


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # BASIC: stessi byte con o senza raqm installato (l'ETag dipende dall'immagine)
    return ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)


def _cmap_codepoints(data: bytes) -> frozenset[int]:
    """Caratteri che il font sa disegnare, dalla tabella `cmap` (formati 4 e 12)."""
    tables = {data[12 + 16 * i:16 + 16 * i]: struct.unpack_from(">I", data, 20 + 16 * i)[0]
              for i in range(struct.unpack_from(">H", data, 4)[0])}
    cmap = tables[b"cmap"]
    subtables = {}
    for i in range(struct.unpack_from(">H", data, cmap + 2)[0]):
        platform, _, offset = struct.unpack_from(">HHI", data, cmap + 4 + 8 * i)
        fmt = struct.unpack_from(">H", data, cmap + offset)[0]
        if platform in (0, 3) and fmt in (4, 12):
            subtables.setdefault(fmt, cmap + offset)
    out: set[int] = set()
    if 12 in subtables:                     # gruppi (inizio, fine, primo glifo)
        at = subtables[12]
        for g in range(struct.unpack_from(">I", data, at + 12)[0]):
            start, end, glyph = struct.unpack_from(">III", data, at + 16 + 12 * g)
            out.update(range(start, min(end, 0x10FFFF) + 1) if glyph else range(start + 1, end + 1))
        return frozenset(out)
    at = subtables[4]                       # segmenti con delta o indirizzo dei glifi
    seg2 = struct.unpack_from(">H", data, at + 6)[0]
    for i in range(0, seg2, 2):
        end = struct.unpack_from(">H", data, at + 14 + i)[0]
        start = struct.unpack_from(">H", data, at + 16 + seg2 + i)[0]
        delta = struct.unpack_from(">h", data, at + 16 + 2 * seg2 + i)[0]
        range_at = at + 16 + 3 * seg2 + i
        range_offset = struct.unpack_from(">H", data, range_at)[0]
        for code in range(start, end + 1):
            if code == 0xFFFF:
                continue
            if range_offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                glyph = struct.unpack_from(">H", data, range_at + range_offset + 2 * (code - start))[0]
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            if glyph:
                out.add(code)
    return frozenset(out)


@lru_cache(maxsize=None)
def glyphs() -> frozenset[int]:
    """I caratteri del font, letti una volta: gli altri (emoji, bandiere, selettori di
    variante, zero-width joiner, modificatori di carnagione) sul pannello sarebbero "[?]"."""
    return _cmap_codepoints(FONT_PATH.read_bytes())


def _clean(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Testo su una riga, solo caratteri che il font disegna, spazi compattati, al massimo
    `limit` caratteri. NFC prima, così un accento combinante diventa "à" e non si perde."""
    have = glyphs()
    text = unicodedata.normalize("NFC", " ".join(str(text)[:limit * 4].split()))
    text = "".join(ch for ch in text if ord(ch) in have and unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())[:limit]


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


def _today_events(events, day: date, now: datetime, tz: ZoneInfo, limit: int) -> list[str]:
    """Eventi di oggi (`day`): tutto il giorno prima, poi per ora di inizio.

    Tutto il giorno: inizio ≤ giorno < fine (fine esclusiva, come la manda HA). A orario:
    non ancora finito e iniziato prima di domani (compresi quelli in corso o a cavallo della
    mezzanotte); quelli iniziati prima di oggi e ancora in corso si mostrano senza ora, come
    quelli di tutto il giorno. Un titolo che resta vuoto senza le emoji non si mostra.
    """
    day_start = datetime.combine(day, time(), tz)
    day_end = datetime.combine(day + timedelta(days=1), time(), tz)
    found: list[tuple] = []
    for event in events[:MAX_EVENTS]:
        summary, calendar = _clean(event.summary), _clean(event.calendar)
        if not summary:
            continue
        if event.all_day:
            start = _date(event.start)
            end = _date(event.end) or (start + timedelta(days=1) if start else None)
            if start and start <= day < end:
                found.append((0, day_start, summary, calendar, summary))
            continue
        start, end = _datetime(event.start, tz), _datetime(event.end, tz)
        if start is None:
            continue
        end = end or start
        keep = end > now and start < day_end
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


def lines(snapshot: Snapshot, now: datetime, tz: ZoneInfo) -> list[tuple[str, str]]:
    """Righe (stile, testo) prima dell'impaginazione; stile in item, rule, small.

    Solo il calendario di oggi, poi (se ci sono sensori) il filetto e i sensori; la spesa
    non è nell'immagine. Due sensori stanno su una riga solo se ci entrano.
    `now` deve avere il fuso (aware), altrimenti ValueError.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("digest: serve un `now` con il fuso orario")
    reading = snapshot.reading
    events = reading.events if reading else ()
    today = now.astimezone(tz).date()
    rows: list[tuple[str, str]] = []
    today_items = _today_events(events, today, now, tz, MAX_TODAY)
    rows += [("item", t) for t in today_items] or [("item", EMPTY_DAY)]

    sensors = (reading.sensors if reading else ())[:MAX_SENSORS]
    values = [v for v in (" ".join(f"{_clean(s.label, 40)} {_sensor_value(s)}".split())
                          for s in sensors) if v]
    font = _font(STYLES["item"][0])
    if values:
        rows.append(("rule", ""))
    while values:
        pair = SENSOR_SEP.join(values[:2])
        if len(values) > 1 and _width(pair, font) <= MAX_TEXT_W:
            rows.append(("item", pair))
            values = values[2:]
        else:
            rows.append(("item", values.pop(0)))

    if snapshot.fetched_at is not None and now - snapshot.fetched_at > STALE_AFTER:
        rows.append(("small", f"agg. {snapshot.fetched_at.astimezone(tz):%H:%M}"))
    return rows


# --- impaginazione -----------------------------------------------------------------------

def _width(text: str, font: ImageFont.FreeTypeFont) -> float:
    """Larghezza occupata disegnando senza sfumature (mode "1", come in render: le misure
    del modo predefinito a 20 px risultano più strette di qualche punto), avanzamento o
    inchiostro se un glifo sporge."""
    if not text:
        return 0
    return max(font.getlength(text, mode="1"), font.getbbox(text, mode="1", anchor="ls")[2])


def _fit(text: str, font: ImageFont.FreeTypeFont) -> str:
    """Il testo, o il suo inizio più "…", entro `MAX_TEXT_W`."""
    text = _clean(text)
    if _width(text, font) <= MAX_TEXT_W:
        return text
    low, high = 0, len(text)        # il prefisso più lungo che sta con "…" (ricerca binaria)
    while low < high:
        mid = (low + high + 1) // 2
        if _width(text[:mid].rstrip() + ELLIPSIS, font) <= MAX_TEXT_W:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + ELLIPSIS


def render(snapshot: Snapshot, now: datetime, tz: ZoneInfo) -> Image.Image:
    """Immagine modo "1" 200×136. Le righe che non stanno si tolgono dal fondo del
    contenuto; la riga "agg." resta sempre in basso. `now` deve avere il fuso."""
    rows = lines(snapshot, now, tz)
    small = [r for r in rows if r[0] == "small"]
    body = [r for r in rows if r[0] != "small"]
    # un po' d'aria sotto la fascia del device; se la prima riga sporgerebbe sopra y = 0
    # (es. maiuscola accentata), si scende quanto basta
    top = TOP_GAP
    if body:
        size, _, baseline = STYLES[body[0][0]]
        font = _font(size)
        top = max(TOP_GAP, 1 - (baseline + font.getbbox(_fit(body[0][1], font), mode="1", anchor="ls")[1]))
    room = HEIGHT - top - sum(STYLES[s][1] for s, _ in small)
    while body and sum(STYLES[s][1] for s, _ in body) > room:
        body.pop()
    while body and body[-1][0] == "rule":       # niente filetto senza sensori sotto
        body.pop()

    image = Image.new("1", (WIDTH, HEIGHT), 1)
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"
    y = top
    for style, text in body:
        size, height, baseline = STYLES[style]
        if style == "rule":
            draw.line((MARGIN_X, y + baseline, WIDTH - MARGIN_X - 1, y + baseline), fill=0)
        else:
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
