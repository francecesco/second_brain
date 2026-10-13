import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from secondbrain import digest
from secondbrain import homeassistant as ha
from secondbrain.ha_store import Snapshot

NOW = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)      # 10:00 a Roma
TZ = ZoneInfo("Europe/Rome")


def ev(summary, start, end, calendar="calendar.casa"):
    return ha.Event(calendar, summary, start, end, len(start) == 10)


def at(hhmm, day=13):
    return f"2026-10-{day:02d}T{hhmm}:00+02:00"


def snap(events=(), shopping=(), sensors=(), fetched_at=NOW):
    return Snapshot(fetched_at, ha.Reading(tuple(events), tuple(shopping), tuple(sensors), ()))


def sensor(label, state, unit=None):
    return ha.SensorValue(f"sensor.{label}", label, state, unit)


def texts(rows):
    return [t for _, t in rows]


def test_events_today_sorted_all_day_first_finished_excluded_max_three_no_tomorrow():
    events = [
        ev("Dentista", at("15:00"), at("16:00")),
        ev("Colazione", at("07:00"), at("08:00")),               # già finito
        ev("Riunione", at("09:30"), at("10:30")),                # in corso
        ev("Compleanno", "2026-10-13", "2026-10-14"),
        ev("Cena", at("20:00"), at("22:00")),                    # quarto: escluso
        ev("Carta", "2026-10-14", "2026-10-15"),
        ev("Palestra", at("18:00", 14), at("19:00", 14)),
    ]
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows[:4] == [("head", "OGGI"), ("item", "Compleanno"), ("item", "09:30 Riunione"),
                        ("item", "15:00 Dentista")]
    assert len(rows) == 4             # solo oggi: niente sezione per domani
    assert not hasattr(digest, "MAX_TOMORROW")


def test_event_across_midnight_is_today_yesterday_all_day_is_not():
    events = [ev("Festa", at("23:30"), at("00:30", 14)),
              ev("Ieri", "2026-10-12", "2026-10-13")]
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows[:2] == [("head", "OGGI"), ("item", "23:30 Festa")]
    assert "Ieri" not in texts(rows)
    assert ("head", "DOMANI") not in rows


def test_nothing_today():
    rows = digest.lines(snap(), NOW, TZ, shopping=False)
    assert rows == [("head", "OGGI"), ("item", digest.EMPTY_DAY)]


def test_shopping_is_one_line_and_omitted_without_list():
    def spesa(n):
        rows = digest.lines(snap(shopping=[f"voce {i}" for i in range(n)]), NOW, TZ, shopping=True)
        assert [s for s, _ in rows] == ["head", "item", "item"]       # senza titolo SPESA
        return rows[-1]
    assert spesa(0) == ("item", "Spesa: vuota")
    assert spesa(1) == ("item", "Spesa: 1 articolo")
    assert spesa(5) == ("item", "Spesa: 5 articoli")
    rows = digest.lines(snap(shopping=["latte"]), NOW, TZ, shopping=False)
    assert not any("Spesa" in t for t in texts(rows))


def test_sensors_rounded_with_comma_one_per_row():
    rows = digest.lines(snap(sensors=[sensor("casa", "21.5", "°C")]), NOW, TZ, shopping=False)
    assert rows[-1] == ("item", "casa 21,5°C")
    rows = digest.lines(snap(sensors=[sensor("casa", "21.46", "°C"), sensor("fuori", "unavailable"),
                                      sensor("umidita bagno", "55", "%")]), NOW, TZ, shopping=False)
    assert rows[-3:] == [("item", "casa 21,5°C"), ("item", "fuori unavailable"),
                         ("item", "umidita bagno 55%")]


def test_short_sensors_share_a_row_when_they_fit():
    rows = digest.lines(snap(sensors=[sensor("t", "21", "°"), sensor("u", "55", "%"),
                                      sensor("pressione", "1013", "hPa")]), NOW, TZ, shopping=False)
    assert rows[-2:] == [("item", "t 21° · u 55%"), ("item", "pressione 1013 hPa")]


def test_stale_label_only_after_thirty_minutes():
    fresh = digest.lines(snap(fetched_at=NOW - timedelta(minutes=29)), NOW, TZ, shopping=False)
    assert not any(style == "small" for style, _ in fresh)
    old = digest.lines(snap(fetched_at=NOW - timedelta(minutes=31)), NOW, TZ, shopping=False)
    assert old[-1] == ("small", "agg. 09:29")


def test_render_size_mode_and_pack_bit_order():
    image = digest.render(snap(), NOW, TZ, shopping=True)
    assert image.mode == "1" and image.size == (200, 136)
    assert len(digest.pack(image)) == digest.PACKED_BYTES == 3400
    white = Image.new("1", (200, 136), 1)
    assert digest.pack(white) == bytes(3400)
    white.putpixel((0, 0), 0)
    assert digest.pack(white)[0] == 0x80
    white.putpixel((0, 0), 1)
    white.putpixel((8, 0), 0)
    assert digest.pack(white)[:2] == b"\x00\x80"


def _black_columns(image):
    return [x for x in range(image.width) for y in range(image.height) if image.getpixel((x, y)) == 0]


def test_long_title_is_cut_with_ellipsis():
    title = "Riunione di condominio straordinaria per il rifacimento della facciata " * 3
    image = digest.render(snap([ev(title, at("15:00"), at("16:00"))]), NOW, TZ, shopping=False)
    assert max(_black_columns(image)) <= 196


def test_same_snapshot_same_etag_one_more_item_differs():
    a = digest.pack(digest.render(snap(shopping=["latte"]), NOW, TZ, shopping=True))
    b = digest.pack(digest.render(snap(shopping=["latte"]), NOW, TZ, shopping=True))
    c = digest.pack(digest.render(snap(shopping=["latte", "pane"]), NOW, TZ, shopping=True))
    assert digest.etag(a) == digest.etag(b)
    assert digest.etag(a) != digest.etag(c)
    assert digest.etag(a).startswith('"') and len(digest.etag(a)) == 18


def test_overflow_keeps_the_stale_label():
    events = [ev(f"Evento {i}", at(f"{11 + i}:00"), at(f"{11 + i}:30")) for i in range(10)]
    sensors = [sensor(f"s{i}", "20", "°C") for i in range(6)]
    image = digest.render(snap(events, ["latte"], sensors, fetched_at=NOW - timedelta(hours=2)),
                          NOW, TZ, shopping=True)
    band = image.crop((0, digest.HEIGHT - digest.STYLES["small"][1], digest.WIDTH, digest.HEIGHT))
    assert band.histogram()[0] > 0


def test_preview_png_is_scaled():
    image = digest.render(snap(), NOW, TZ, shopping=False)
    png = digest.preview_png(image, scale=3)
    assert png.startswith(b"\x89PNG")


def test_tomorrow_events_are_never_shown():
    events = [ev("Vacanza", "2026-10-12", "2026-10-16"),
              ev("Carta", "2026-10-14", "2026-10-15"),
              ev("Palestra", at("18:00", 14), at("19:00", 14))]
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows == [("head", "OGGI"), ("item", "Vacanza")]


def test_emoji_and_characters_without_glyph_are_removed():
    events = [ev("🎂 Compleanno di Giulia 🎉", "2026-10-13", "2026-10-14"),
              ev("☕️Caffè", at("11:00"), at("11:30")),
              ev("🇮🇹 Partita 👍🏽", at("12:00"), at("13:00")),
              ev("👨‍👩‍👧", at("14:00"), at("15:00"))]            # resta vuoto: si salta
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows == [("head", "OGGI"), ("item", "Compleanno di Giulia"), ("item", "11:00 Caffè"),
                    ("item", "12:00 Partita")]
    rows = digest.lines(snap(sensors=[sensor("🌡️ casa", "☀️ sole")]), NOW, TZ, shopping=False)
    assert rows[-1] == ("item", "casa sole")


def test_italian_accents_and_symbols_survive():
    title = "àèéìòù ÀÈÉÌÒÙ 21° · città…"
    rows = digest.lines(snap([ev(title, "2026-10-13", "2026-10-14")]), NOW, TZ, shopping=False)
    assert rows[1] == ("item", title)
    decomposed = "citta\u0300"                                       # accento combinante
    rows = digest.lines(snap([ev(decomposed, "2026-10-13", "2026-10-14")]), NOW, TZ, shopping=False)
    assert rows[1] == ("item", "città")


def test_glyph_set_matches_the_font_cmap():
    ttlib = pytest.importorskip("fontTools.ttLib")
    cmap = ttlib.TTFont(str(digest.FONT_PATH)).getBestCmap()
    assert digest.glyphs() == frozenset(cmap)


def test_timed_event_started_yesterday_has_no_time():
    events = [ev("Turno", "2026-10-12T22:00:00+02:00", at("11:00")),
              ev("Riunione", at("09:30"), at("10:30"))]
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows[1:3] == [("item", "Turno"), ("item", "09:30 Riunione")]


def test_naive_now_is_refused():
    with pytest.raises(ValueError):
        digest.lines(snap(), NOW.replace(tzinfo=None), TZ, shopping=False)


def test_huge_inputs_render_fast():
    events = [ev("x" * 1_000_000, at("15:00"), at("16:00"))]
    events += [ev(f"Evento {i}", at("17:00"), at("18:00")) for i in range(10_000)]
    data = snap(events, [f"voce {i}" for i in range(1000)],
                [sensor("casa", "1" * 100_000, "°C" * 1000)] * 50)
    started = time.perf_counter()
    image = digest.render(data, NOW, TZ, shopping=True)
    packed = digest.pack(image)
    assert time.perf_counter() - started < 1.0
    assert len(packed) == digest.PACKED_BYTES
    assert max(_black_columns(image)) <= 196


def _ink_rows(image):
    return [y for y in range(image.height) if any(image.getpixel((x, y)) == 0 for x in range(image.width))]


def test_first_heading_is_not_clipped_at_the_top():
    image = digest.render(snap([ev("Riunione", at("15:00"), at("16:00"))]), NOW, TZ, shopping=False)
    rows = _ink_rows(image)
    assert rows[0] >= 1             # fila y = 0 tutta bianca
    assert rows[0] <= 4             # ma il titolo non scende troppo
