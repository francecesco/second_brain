"""Connettore di sola lettura per Home Assistant (REST API).

Legge calendari, sensori e lista della spesa con un token di accesso a lungo termine.
Ogni errore diventa un `HaError` con un testo da mostrare all'utente: mai il token né gli
header della richiesta.
"""
from dataclasses import dataclass
from datetime import datetime

import httpx

TIMEOUT_S = 10.0
DOMAINS = ("calendar", "sensor", "todo")


class HaError(Exception):
    """Messaggio da mostrare all'utente; mai il token."""


@dataclass(frozen=True)
class SensorChoice:
    entity_id: str
    label: str | None = None


@dataclass(frozen=True)
class HaConfig:
    url: str
    calendars: tuple[str, ...] = ()
    sensors: tuple[SensorChoice, ...] = ()
    shopping_list: str | None = None


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str          # friendly_name, o l'entity_id


@dataclass(frozen=True)
class Event:
    calendar: str      # entity_id
    summary: str
    start: str         # come arriva da HA: "2026-10-13" o "2026-10-13T09:30:00+02:00"
    end: str
    all_day: bool


@dataclass(frozen=True)
class SensorValue:
    entity_id: str
    label: str
    state: str
    unit: str | None


@dataclass(frozen=True)
class Reading:
    events: tuple[Event, ...]
    shopping: tuple[str, ...]
    sensors: tuple[SensorValue, ...]
    missing: tuple[str, ...]   # entità scelte che HA non elenca più


def clean_url(raw: str) -> str:
    """Indirizzo senza spazi né "/" finale; solo http o https."""
    url = (raw or "").strip().rstrip("/")
    host = url.split("://", 1)[1] if url.startswith(("http://", "https://")) else ""
    if not host:
        raise HaError("indirizzo di Home Assistant non valido (serve http:// o https://)")
    return url


def _is_ssl_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "certificate" in text or "ssl" in text


def _request(client: httpx.Client, method: str, url: str, token: str,
             json=None, params=None) -> httpx.Response:
    """Chiamata con token e timeout; eccezioni httpx e status di errore diventano `HaError`."""
    try:
        response = client.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                  json=json, params=params, timeout=TIMEOUT_S)
    except httpx.TimeoutException:
        raise HaError("Home Assistant non risponde (timeout)") from None
    except httpx.ConnectError as exc:
        if _is_ssl_error(exc):
            raise HaError("certificato di Home Assistant non valido") from None
        raise HaError("Home Assistant irraggiungibile") from None
    except httpx.HTTPError:
        raise HaError("Home Assistant irraggiungibile") from None
    if response.status_code in (401, 403):
        raise HaError("token rifiutato da Home Assistant (401)")
    if response.status_code >= 400:
        raise HaError(f"Home Assistant ha risposto {response.status_code}")
    return response


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        raise HaError("risposta di Home Assistant non valida") from None


def _service_response(data) -> dict:
    """Corpo `service_response` di una chiamata con `return_response` (HA 2023.12+)."""
    inner = data.get("service_response") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        raise HaError("serve Home Assistant 2023.12 o successivo")
    return inner


def _states(client: httpx.Client, url: str, token: str) -> list[dict]:
    data = _json(_request(client, "GET", f"{url}/api/states", token))
    if not isinstance(data, list):
        raise HaError("risposta di Home Assistant non valida")
    return [s for s in data if isinstance(s, dict) and isinstance(s.get("entity_id"), str)]


def _attributes(state: dict) -> dict:
    attrs = state.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def test(client: httpx.Client, url: str, token: str) -> str:
    """Verifica indirizzo e token: "API running." oppure `HaError`."""
    data = _json(_request(client, "GET", f"{url}/api/", token))
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str):
        raise HaError("risposta di Home Assistant non valida")
    return message


def list_entities(client: httpx.Client, url: str, token: str) -> dict[str, list[Entity]]:
    """Calendari, sensori e liste (`todo`) disponibili, ordinati per nome."""
    found: dict[str, list[Entity]] = {d: [] for d in DOMAINS}
    for state in _states(client, url, token):
        entity_id = state["entity_id"]
        domain = entity_id.split(".", 1)[0]
        if domain in found:
            name = _attributes(state).get("friendly_name")
            found[domain].append(Entity(entity_id, name if isinstance(name, str) and name else entity_id))
    for entities in found.values():
        entities.sort(key=lambda e: e.name.lower())
    return found


def read(client: httpx.Client, config: HaConfig, token: str,
         start: datetime, end: datetime) -> Reading:
    """Eventi tra `start` e `end`, voci da comprare e valori dei sensori scelti."""
    url = config.url
    states = {s["entity_id"]: s for s in _states(client, url, token)}
    missing: list[str] = []

    sensors: list[SensorValue] = []
    for choice in config.sensors:
        state = states.get(choice.entity_id)
        if state is None:
            missing.append(choice.entity_id)
            continue
        attrs = _attributes(state)
        unit = attrs.get("unit_of_measurement")
        label = choice.label or attrs.get("friendly_name") or choice.entity_id
        sensors.append(SensorValue(choice.entity_id, str(label), str(state.get("state")),
                                   unit if isinstance(unit, str) else None))

    calendars = [c for c in config.calendars if c in states]
    missing += [c for c in config.calendars if c not in states]
    events: list[Event] = []
    if calendars:
        body = {"entity_id": calendars, "start_date_time": start.isoformat(),
                "end_date_time": end.isoformat()}
        data = _json(_request(client, "POST", f"{url}/api/services/calendar/get_events", token,
                              json=body, params={"return_response": "true"}))
        response = _service_response(data)
        for calendar in calendars:
            entry = response.get(calendar)
            items = entry.get("events") if isinstance(entry, dict) else None
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                ev_start, ev_end = str(item.get("start", "")), str(item.get("end", ""))
                events.append(Event(calendar, str(item.get("summary", "")), ev_start, ev_end,
                                    len(ev_start) == 10))

    shopping: list[str] = []
    if config.shopping_list:
        if config.shopping_list not in states:
            missing.append(config.shopping_list)
        else:
            body = {"entity_id": config.shopping_list, "status": ["needs_action"]}
            data = _json(_request(client, "POST", f"{url}/api/services/todo/get_items", token,
                                  json=body, params={"return_response": "true"}))
            entry = _service_response(data).get(config.shopping_list)
            items = entry.get("items") if isinstance(entry, dict) else None
            shopping = [str(i["summary"]) for i in items if isinstance(i, dict) and "summary" in i] \
                if isinstance(items, list) else []

    return Reading(tuple(events), tuple(shopping), tuple(sensors), tuple(missing))
