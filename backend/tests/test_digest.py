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


def test_events_today_sorted_all_day_first_finished_excluded_max_three():
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
    assert rows[4:7] == [("head", "DOMANI"), ("item", "Carta"), ("item", "18:00 Palestra")]
    assert "Carta" not in texts(rows[:4])
    assert "Cena" not in " ".join(texts(rows))


def test_event_across_midnight_is_today_yesterday_all_day_is_not():
    events = [ev("Festa", at("23:30"), at("00:30", 14)),
              ev("Ieri", "2026-10-12", "2026-10-13")]
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows[:2] == [("head", "OGGI"), ("item", "23:30 Festa")]
    assert "Ieri" not in texts(rows)
    assert ("head", "DOMANI") not in rows


def test_nothing_today_and_tomorrow_section_omitted():
    rows = digest.lines(snap(), NOW, TZ, shopping=False)
    assert rows == [("head", "OGGI"), ("item", "niente in programma")]


def test_shopping_counts_and_section_omitted_without_list():
    def spesa(n):
        rows = digest.lines(snap(shopping=[f"voce {i}" for i in range(n)]), NOW, TZ, shopping=True)
        i = rows.index(("head", "SPESA"))
        return rows[i + 1]
    assert spesa(0) == ("item", "vuota")
    assert spesa(1) == ("item", "1 articolo")
    assert spesa(5) == ("item", "5 articoli")
    rows = digest.lines(snap(shopping=["latte"]), NOW, TZ, shopping=False)
    assert ("head", "SPESA") not in rows


def test_sensors_rounded_with_comma_two_per_row():
    rows = digest.lines(snap(sensors=[sensor("casa", "21.5", "°C")]), NOW, TZ, shopping=False)
    assert rows[-1] == ("item", "casa 21,5°C")
    rows = digest.lines(snap(sensors=[sensor("casa", "21.46", "°C"), sensor("fuori", "unavailable"),
                                      sensor("umid", "55", "%")]), NOW, TZ, shopping=False)
    assert rows[-2:] == [("item", "casa 21,5°C · fuori unavailable"), ("item", "umid 55%")]


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
    band = image.crop((0, digest.HEIGHT - 12, digest.WIDTH, digest.HEIGHT))
    assert band.histogram()[0] > 0


def test_preview_png_is_scaled():
    image = digest.render(snap(), NOW, TZ, shopping=False)
    png = digest.preview_png(image, scale=3)
    assert png.startswith(b"\x89PNG")


def test_tomorrow_does_not_repeat_all_day_events_of_today():
    events = [ev("Vacanza", "2026-10-12", "2026-10-16"),
              ev("Carta", "2026-10-14", "2026-10-15"),
              ev("Palestra", at("18:00", 14), at("19:00", 14))]
    rows = digest.lines(snap(events), NOW, TZ, shopping=False)
    assert rows == [("head", "OGGI"), ("item", "Vacanza"), ("head", "DOMANI"),
                    ("item", "Carta"), ("item", "18:00 Palestra")]


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
