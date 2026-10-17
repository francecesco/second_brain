"""Liste raggruppate per giorno ("Oggi", "Ieri", "Martedì 14 ottobre"): Da fare e Completati
(spec UI responsive §2.2)."""
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from .templating import MONTHS, WEEKDAYS


@dataclass
class DayGroup:
    day: date
    label: str
    items: list = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.day.strftime("%d/%m")

    @property
    def key(self) -> str:
        return self.day.isoformat()


def day_heading(day: date, today: date) -> str:
    if day == today:
        return "Oggi"
    if day == today - timedelta(days=1):
        return "Ieri"
    label = f"{WEEKDAYS[day.weekday()].capitalize()} {day.day} {MONTHS[day.month - 1]}"
    return label if day.year == today.year else f"{label} {day.year}"


def day_groups(items: Iterable, key: Callable[[object], date], today: date) -> list[DayGroup]:
    """Raggruppa elementi già ordinati: un gruppo per giorno consecutivo, ordine invariato."""
    groups: list[DayGroup] = []
    for item in items:
        day = key(item)
        if not groups or groups[-1].day != day:
            groups.append(DayGroup(day, day_heading(day, today)))
        groups[-1].items.append(item)
    return groups
