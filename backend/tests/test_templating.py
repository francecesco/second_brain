"""Nomi dei giorni della settimana e titolo di default (solo visualizzazione)."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain.web.templating import default_title, weekday_label, weekday_short

ROME = ZoneInfo("Europe/Rome")


@pytest.mark.parametrize("d,label,short", [
    (date(2026, 9, 28), "Lunedì 28", "lun 28"),  # lunedì
    (date(2026, 9, 27), "Domenica 27", "dom 27"),  # domenica
])
def test_weekday_label_and_short(d, label, short):
    assert weekday_label(d) == label
    assert weekday_short(d) == short


@pytest.mark.parametrize("utc_hour,utc_minute,expected", [
    (2, 59, "Nota della notte, 04:59"),
    (3, 0, "Nota del mattino, 05:00"),
    (9, 59, "Nota del mattino, 11:59"),
    (10, 0, "Nota del pomeriggio, 12:00"),
    (15, 59, "Nota del pomeriggio, 17:59"),
    (16, 0, "Nota della sera, 18:00"),
    (20, 59, "Nota della sera, 22:59"),
    (21, 0, "Nota della notte, 23:00"),
])
def test_default_title_bands_and_boundaries(utc_hour, utc_minute, expected):
    recorded_at = datetime(2026, 9, 28, utc_hour, utc_minute, tzinfo=UTC)
    assert default_title(recorded_at, ROME) == expected


def test_default_title_dst_example_from_the_brief():
    recorded_at = datetime(2026, 9, 29, 8, 32, tzinfo=UTC)
    assert default_title(recorded_at, ROME) == "Nota del mattino, 10:32"
