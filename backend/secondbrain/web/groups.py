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


SOON_DAYS = 7  # "Prossimi 7 giorni" nella sezione In scadenza (spec azioni §2.3)


@dataclass
class DueGroup:
    key: str        # overdue, today, soon
    label: str
    items: list = field(default_factory=list)   # (capture, action, data di scadenza)


def due_groups(pairs: Iterable, today: date) -> list[DueGroup]:
    """Azioni aperte con scadenza (coppie nota-azione del catalogo) in Scadute, Oggi e
    Prossimi 7 giorni, ognuna per data; le altre restano fuori. Gruppi vuoti tolti."""
    groups = {"overdue": DueGroup("overdue", "Scadute"), "today": DueGroup("today", "Oggi"),
              "soon": DueGroup("soon", f"Prossimi {SOON_DAYS} giorni")}
    for capture, action in pairs:
        if not action.get("due") or action.get("done"):
            continue
        due = date.fromisoformat(action["due"])
        if due < today:
            key = "overdue"
        elif due == today:
            key = "today"
        elif due <= today + timedelta(days=SOON_DAYS):
            key = "soon"
        else:
            continue
        groups[key].items.append((capture, action, due))
    for group in groups.values():
        group.items.sort(key=lambda item: item[2])
    return [g for g in groups.values() if g.items]


def day_groups(items: Iterable, key: Callable[[object], date], today: date) -> list[DayGroup]:
    """Raggruppa elementi già ordinati: un gruppo per giorno consecutivo, ordine invariato."""
    groups: list[DayGroup] = []
    for item in items:
        day = key(item)
        if not groups or groups[-1].day != day:
            groups.append(DayGroup(day, day_heading(day, today)))
        groups[-1].items.append(item)
    return groups
