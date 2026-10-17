import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from secondbrain import ha_store
from secondbrain import homeassistant as ha
from secondbrain import settings_store as store
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.worker import HA_REFRESH_S, Worker
from tests.ai_fakes import FakeFactory
from tests.test_homeassistant import TOKEN, URL, fake, hass

ROME = ZoneInfo("Europe/Rome")
CONFIG = ha.HaConfig(url=URL, calendars=("calendar.casa",), shopping_list="todo.shopping_list")


def make_worker(db, settings, clock, handler=hass):
    box = store.SecretBox(settings.settings_key)
    worker = Worker(sessionmaker=make_sessionmaker(db.get_bind()),
                    archive=Archive(settings.archive_dir), tz=settings.tz_archive, box=box,
                    factory=FakeFactory(), clock=clock, http=fake(handler))
    return worker, box


def configure(db, box):
    ha_store.save_connection(db, box, URL, TOKEN)
    ha_store.save_choices(db, CONFIG.calendars, [], CONFIG.shopping_list)
    db.commit()


def snapshot(db):
    db.expire_all()
    return ha_store.get_snapshot(db)


def test_without_configuration(db, settings, clock):
    worker, _ = make_worker(db, settings, clock)
    assert worker.maybe_refresh_homeassistant() is False
    assert snapshot(db).reading is None


def test_without_http_client(db, settings, clock):
    worker, box = make_worker(db, settings, clock)
    configure(db, box)
    worker.http = None
    assert worker.maybe_refresh_homeassistant() is False


def test_reads_then_waits_for_the_interval(db, settings, clock):
    worker, box = make_worker(db, settings, clock)
    configure(db, box)
    assert worker.maybe_refresh_homeassistant() is True
    assert snapshot(db).reading.shopping == ("latte", "pane")
    assert worker.maybe_refresh_homeassistant() is False
    clock.now += timedelta(seconds=HA_REFRESH_S + 1)
    assert worker.maybe_refresh_homeassistant() is True


def test_requested_refresh_skips_the_interval(db, settings, clock):
    worker, box = make_worker(db, settings, clock)
    configure(db, box)
    assert worker.maybe_refresh_homeassistant() is True
    assert worker.maybe_refresh_homeassistant() is False
    clock.now += timedelta(seconds=10)
    ha_store.request_refresh(db, clock.now)
    db.commit()
    assert worker.maybe_refresh_homeassistant() is True


def test_error_keeps_the_previous_snapshot(db, settings, clock):
    state = {"fail": False}

    def handler(request):
        return httpx.Response(500) if state["fail"] else hass(request)

    worker, box = make_worker(db, settings, clock, handler)
    configure(db, box)
    assert worker.maybe_refresh_homeassistant() is True
    state["fail"] = True
    clock.now += timedelta(seconds=HA_REFRESH_S + 1)
    assert worker.maybe_refresh_homeassistant() is True
    snap = snapshot(db)
    assert snap.reading.shopping == ("latte", "pane")
    assert snap.error and TOKEN not in snap.error


def test_window_covers_two_weeks_for_the_actions_context(db, settings, clock):
    clock.now = datetime(2026, 10, 13, 22, 30, tzinfo=timezone.utc)  # 00:30 del 14 a Roma
    seen = []

    def handler(request):
        if request.url.path == "/api/services/calendar/get_events":
            seen.append(json.loads(request.content))
        return hass(request)

    worker, box = make_worker(db, settings, clock, handler)
    configure(db, box)
    assert worker.maybe_refresh_homeassistant() is True
    end = datetime.fromisoformat(seen[0]["end_date_time"])
    assert end == datetime(2026, 10, 29, 0, 0, tzinfo=ROME)    # oggi + 14 giorni interi (spec azioni §4)
    assert datetime.fromisoformat(seen[0]["start_date_time"]) == clock.now
