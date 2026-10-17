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


def test_events_today_sorted_all_day_first_finished_excluded_max_four_no_tomorrow():
    events = [
        ev("Dentista", at("15:00"), at("16:00")),
        ev("Colazione", at("07:00"), at("08:00")),               # già finito
        ev("Riunione", at("09:30"), at("10:30")),                # in corso
        ev("Compleanno", "2026-10-13", "2026-10-14"),
        ev("Cena", at("20:00"), at("22:00")),
        ev("Tardi", at("22:30"), at("23:00")),                   # quinto: escluso
        ev("Carta", "2026-10-14", "2026-10-15"),
        ev("Palestra", at("18:00", 14), at("19:00", 14)),
    ]
    rows = digest.lines(snap(events), NOW, TZ)
    assert rows == [("item", "Compleanno"), ("item", "09:30 Riunione"),
                    ("item", "15:00 Dentista"), ("item", "20:00 Cena")]
    assert len(rows) == 4             # solo oggi: niente sezione per domani
    assert not hasattr(digest, "MAX_TOMORROW")


def test_event_across_midnight_is_today_yesterday_all_day_is_not():
    events = [ev("Festa", at("23:30"), at("00:30", 14)),
              ev("Ieri", "2026-10-12", "2026-10-13")]
    rows = digest.lines(snap(events), NOW, TZ)
    assert rows == [("item", "23:30 Festa")]
    assert "Ieri" not in texts(rows)
    assert ("head", "DOMANI") not in rows


def test_nothing_today():
    rows = digest.lines(snap(), NOW, TZ)
    assert rows == [("item", digest.EMPTY_DAY)]


def test_no_heading_and_no_shopping_line_even_with_items():
    rows = digest.lines(snap([ev("Riunione", at("15:00"), at("16:00"))], shopping=["latte", "pane"]),
                        NOW, TZ)
    assert rows == [("item", "15:00 Riunione")]
    assert not any(style == "head" for style, _ in rows)
    assert "shopping" not in digest.lines.__code__.co_varnames
    image = digest.render(snap(shopping=["latte", "pane"]), NOW, TZ)
    assert digest.pack(image) == digest.pack(digest.render(snap(), NOW, TZ))


def test_sensors_whole_numbers_one_per_row():
    rows = digest.lines(snap(sensors=[sensor("casa", "21.5", "°C")]), NOW, TZ)
    assert rows == [("item", digest.EMPTY_DAY), ("rule", ""), ("item", "casa 22°C")]
    rows = digest.lines(snap(sensors=[sensor("casa", "21.46", "°C"), sensor("fuori", "unavailable"),
                                      sensor("umidita bagno", "55", "%")]), NOW, TZ)
    assert rows[-3:] == [("item", "casa 21°C"), ("item", "fuori unavailable"),
                         ("item", "umidita bagno 55%")]


def test_sensor_rounding_half_away_from_zero():
    def show(state, unit="°C"):
        return digest._sensor_value(sensor("x", state, unit))
    assert show("18.5") == "19°C"
    assert show("22.4") == "22°C"
    assert show("-0.4") == "0°C"
    assert show("-1.5") == "-2°C"
    assert show("55", "%") == "55%"
    assert show("1013.4", "hPa") == "1013 hPa"
    assert show("unavailable") == "unavailable"
    assert show("on", "") == "on"


def test_sensor_decimal_drift_keeps_same_etag():
    def image(value):
        return digest.pack(digest.render(snap(sensors=[sensor("giardino", value, "°C")]), NOW, TZ))
    assert digest.etag(image("21.4")) == digest.etag(image("21.2"))
    assert digest.etag(image("21.4")) != digest.etag(image("22.6"))


def test_short_sensors_share_a_row_when_they_fit():
    rows = digest.lines(snap(sensors=[sensor("t", "21", "°"), sensor("u", "55", "%"),
                                      sensor("pressione", "1013", "hPa")]), NOW, TZ)
    assert rows[-2:] == [("item", "t 21° · u 55%"), ("item", "pressione 1013 hPa")]


def test_stale_label_only_after_thirty_minutes():
    fresh = digest.lines(snap(fetched_at=NOW - timedelta(minutes=29)), NOW, TZ)
    assert not any(style == "small" for style, _ in fresh)
    old = digest.lines(snap(fetched_at=NOW - timedelta(minutes=31)), NOW, TZ)
    assert old[-1] == ("small", "agg. 09:29")


def test_render_size_mode_and_pack_bit_order():
    image = digest.render(snap(), NOW, TZ)
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
    image = digest.render(snap([ev(title, at("15:00"), at("16:00"))]), NOW, TZ)
    assert max(_black_columns(image)) <= 196


def test_same_snapshot_same_etag_one_more_item_differs():
    one = [ev("Riunione", at("15:00"), at("16:00"))]
    a = digest.pack(digest.render(snap(one), NOW, TZ))
    b = digest.pack(digest.render(snap(one), NOW, TZ))
    c = digest.pack(digest.render(snap(one + [ev("Cena", at("20:00"), at("21:00"))]), NOW, TZ))
    assert digest.etag(a) == digest.etag(b)
    assert digest.etag(a) != digest.etag(c)
    assert digest.etag(a).startswith('"') and len(digest.etag(a)) == 18


def test_overflow_keeps_the_stale_label():
    events = [ev(f"Evento {i}", at(f"{11 + i}:00"), at(f"{11 + i}:30")) for i in range(10)]
    sensors = [sensor(f"s{i}", "20", "°C") for i in range(6)]
    image = digest.render(snap(events, ["latte"], sensors, fetched_at=NOW - timedelta(hours=2)),
                          NOW, TZ)
    band = image.crop((0, digest.HEIGHT - digest.STYLES["small"][1], digest.WIDTH, digest.HEIGHT))
    assert band.histogram()[0] > 0


def test_preview_png_is_scaled():
    image = digest.render(snap(), NOW, TZ)
    png = digest.preview_png(image, scale=3)
    assert png.startswith(b"\x89PNG")


def test_tomorrow_events_are_never_shown():
    events = [ev("Vacanza", "2026-10-12", "2026-10-16"),
              ev("Carta", "2026-10-14", "2026-10-15"),
              ev("Palestra", at("18:00", 14), at("19:00", 14))]
    rows = digest.lines(snap(events), NOW, TZ)
    assert rows == [("item", "Vacanza")]


def test_emoji_and_characters_without_glyph_are_removed():
    events = [ev("🎂 Compleanno di Giulia 🎉", "2026-10-13", "2026-10-14"),
              ev("☕️Caffè", at("11:00"), at("11:30")),
              ev("🇮🇹 Partita 👍🏽", at("12:00"), at("13:00")),
              ev("👨‍👩‍👧", at("14:00"), at("15:00"))]            # resta vuoto: si salta
    rows = digest.lines(snap(events), NOW, TZ)
    assert rows == [("item", "Compleanno di Giulia"), ("item", "11:00 Caffè"),
                    ("item", "12:00 Partita")]
    rows = digest.lines(snap(sensors=[sensor("🌡️ casa", "☀️ sole")]), NOW, TZ)
    assert rows[-1] == ("item", "casa sole")


def test_italian_accents_and_symbols_survive():
    title = "àèéìòù ÀÈÉÌÒÙ 21° · città…"
    rows = digest.lines(snap([ev(title, "2026-10-13", "2026-10-14")]), NOW, TZ)
    assert rows[0] == ("item", title)
    decomposed = "citta\u0300"                                       # accento combinante
    rows = digest.lines(snap([ev(decomposed, "2026-10-13", "2026-10-14")]), NOW, TZ)
    assert rows[0] == ("item", "città")


def test_glyph_set_matches_the_font_cmap():
    ttlib = pytest.importorskip("fontTools.ttLib")
    cmap = ttlib.TTFont(str(digest.FONT_PATH)).getBestCmap()
    assert digest.glyphs() == frozenset(cmap)


def test_timed_event_started_yesterday_has_no_time():
    events = [ev("Turno", "2026-10-12T22:00:00+02:00", at("11:00")),
              ev("Riunione", at("09:30"), at("10:30"))]
    rows = digest.lines(snap(events), NOW, TZ)
    assert rows[:2] == [("item", "Turno"), ("item", "09:30 Riunione")]


def test_naive_now_is_refused():
    with pytest.raises(ValueError):
        digest.lines(snap(), NOW.replace(tzinfo=None), TZ)


def test_huge_inputs_render_fast():
    events = [ev("x" * 1_000_000, at("15:00"), at("16:00"))]
    events += [ev(f"Evento {i}", at("17:00"), at("18:00")) for i in range(10_000)]
    data = snap(events, [f"voce {i}" for i in range(1000)],
                [sensor("casa", "1" * 100_000, "°C" * 1000)] * 50)
    started = time.perf_counter()
    image = digest.render(data, NOW, TZ)
    packed = digest.pack(image)
    assert time.perf_counter() - started < 1.0
    assert len(packed) == digest.PACKED_BYTES
    assert max(_black_columns(image)) <= 196


def _ink_rows(image):
    return [y for y in range(image.height) if any(image.getpixel((x, y)) == 0 for x in range(image.width))]


def test_first_line_starts_a_few_points_below_the_top():
    image = digest.render(snap([ev("Riunione", at("15:00"), at("16:00"))]), NOW, TZ)
    rows = _ink_rows(image)
    assert 3 <= rows[0] <= 8        # sotto la fascia nera del device, mai sopra y = 0


def test_accented_capital_is_not_clipped_at_the_top():
    image = digest.render(snap([ev("ÈCCO", at("15:00"), at("16:00"))]), NOW, TZ)
    assert _ink_rows(image)[0] >= 1


def _full_rules(image):
    """Righe di y con un tratto nero continuo lungo quasi tutta la larghezza."""
    out = []
    for y in range(image.height):
        run = best = 0
        for x in range(image.width):
            run = run + 1 if image.getpixel((x, y)) == 0 else 0
            best = max(best, run)
        if best >= 150:
            out.append(y)
    return out


def test_rule_only_between_events_and_sensors():
    events = [ev("Riunione", at("15:00"), at("16:00"))]
    one = [sensor("casa", "21", "°C")]
    both = digest.render(snap(events, sensors=one), NOW, TZ)
    rules = _full_rules(both)
    assert len(rules) == 1
    ink = _ink_rows(both)
    above = [y for y in ink if y < rules[0]]
    below = [y for y in ink if y > rules[0]]
    assert above and below
    assert rules[0] - above[-1] >= 3 and below[0] - rules[0] >= 3    # aria da entrambi i lati
    assert _full_rules(digest.render(snap(events), NOW, TZ)) == []
    assert len(_full_rules(digest.render(snap(sensors=one), NOW, TZ))) == 1   # anche a giorno vuoto


def test_four_events_two_sensors_and_rule_fit_without_clipping():
    events = [ev(f"Evento {i}", at(f"{11 + i}:00"), at(f"{11 + i}:30")) for i in range(4)]
    sensors = [sensor("temperatura soggiorno", "21", "°C"), sensor("umidita bagno lunga", "55", "%")]
    rows = digest.lines(snap(events, sensors=sensors), NOW, TZ)
    assert [s for s, _ in rows] == ["item"] * 4 + ["rule", "item", "item"]
    image = digest.render(snap(events, sensors=sensors), NOW, TZ)
    assert len(_full_rules(image)) == 1
    assert _ink_rows(image)[-1] >= digest.HEIGHT - 8        # l'ultimo sensore c'è, dentro l'immagine


def test_stale_label_stays_at_the_bottom_and_sensors_go_first():
    events = [ev(f"Evento {i}", at(f"{11 + i}:00"), at(f"{11 + i}:30")) for i in range(4)]
    sensors = [sensor("temperatura soggiorno", "21", "°C"), sensor("umidita bagno lunga", "55", "%")]
    image = digest.render(snap(events, sensors=sensors, fetched_at=NOW - timedelta(hours=2)),
                          NOW, TZ)
    band = image.crop((0, digest.HEIGHT - 14, digest.WIDTH, digest.HEIGHT))
    assert band.histogram()[0] > 0
    above = image.crop((0, 0, digest.WIDTH, digest.HEIGHT - 14))
    assert above.histogram()[0] > 0
    # il filetto non resta orfano: se i sensori non ci stanno, salta anche lui
    rules = _full_rules(image)
    assert all(y < digest.HEIGHT - 14 for y in rules)
    if rules:
        assert any(y > rules[0] for y in _ink_rows(above))


# --- azioni (spec azioni §6) ---

def test_actions_come_first_overdue_marked_and_capped():
    from datetime import date as d
    from secondbrain.digest import action_rows, lines
    today = d(2026, 10, 17)
    acts = [("Pagare", d(2026, 10, 15)), ("Chiamare", today), ("Futura", d(2026, 10, 20)),
            ("Senza data", None)]
    assert action_rows(acts, today) == [("item", "! Pagare"), ("item", "Chiamare")]
    many = [(f"a{i}", today) for i in range(5)]
    assert action_rows(many, today) == [("item", "a0"), ("item", "a1"), ("item", "+3 azioni")]
    now = datetime(2026, 10, 17, 8, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert lines(None, now, ZoneInfo("Europe/Rome"), acts) == [("item", "! Pagare"), ("item", "Chiamare")]
    with_ha = lines(Snapshot(now, ha.Reading((), (), (), ())), now, ZoneInfo("Europe/Rome"), acts)
    assert with_ha[:3] == [("item", "! Pagare"), ("item", "Chiamare"), ("rule", "")]
    assert with_ha[3] == ("item", "nulla in programma")
