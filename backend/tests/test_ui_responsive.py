"""UI responsive (spec 2026-10-17): Da fare come home, gruppi per giorno, tessere, albero."""
from datetime import date, timedelta

from secondbrain import catalog, library
from secondbrain.archive import Archive
from secondbrain.web.groups import day_groups, day_heading
from tests.helpers import DEV, NOW, PASSWORD, capture_by, htmx, make_capture

TODAY = date(2026, 9, 28)  # NOW in ora di Roma
CID = "cap_20260923_191530"


# --- intestazioni e gruppi (funzioni pure) ---

def test_day_heading_names_today_yesterday_and_older_days():
    assert day_heading(TODAY, TODAY) == "Oggi"
    assert day_heading(TODAY - timedelta(days=1), TODAY) == "Ieri"
    assert day_heading(date(2026, 9, 22), TODAY) == "Martedì 22 settembre"
    assert day_heading(date(2025, 9, 22), TODAY) == "Lunedì 22 settembre 2025"
    assert day_heading(TODAY + timedelta(days=1), TODAY) == "Martedì 29 settembre"   # data futura stimata


def test_day_groups_keep_order_and_split_by_key():
    a = make_capture(capture_id="a", day=date(2026, 9, 28))
    b = make_capture(capture_id="b", day=date(2026, 9, 28))
    c = make_capture(capture_id="c", day=date(2026, 9, 23))
    groups = day_groups([a, b, c], lambda cap: cap.day, TODAY)
    assert [(g.label, g.short, [x.capture_id for x in g.items]) for g in groups] == [
        ("Oggi", "28/09", ["a", "b"]), ("Mercoledì 23 settembre", "23/09", ["c"])]
    assert groups[0].key == "2026-09-28"
    assert day_groups([], lambda cap: cap.day, TODAY) == []


# --- home ---

def test_home_and_login_go_to_todo(ui):
    r = ui.client.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/todo")
    r = ui.client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/todo")


def test_brand_links_to_todo(recordings):
    text = recordings.client.get("/browse").text
    assert 'class="brand" href="/todo"' in text


# --- Da fare e Completati raggruppati ---

def test_todo_is_grouped_by_day_with_time_only(recordings):
    text = recordings.client.get("/todo").text
    assert text.count('class="day-group"') == 2
    assert "Mercoledì 23 settembre" in text and "Lunedì 10 agosto" in text
    assert text.index("Mercoledì 23 settembre") < text.index("Lunedì 10 agosto")
    assert 'title="23/09/2026 21:15:30">21:15</time>' in text          # ora breve, completa al passaggio
    assert "Registrazioni non ancora spuntate" not in text


def test_done_is_grouped_by_completion_day(recordings, db, settings):
    arch = Archive(settings.archive_dir)
    library.set_done(db, arch, capture_by(db, CID).id, True, NOW)
    library.set_done(db, arch, capture_by(db, "cap_20260810_070000").id, True, NOW - timedelta(days=1))
    db.commit()
    text = recordings.client.get("/done").text
    assert text.count('class="day-group"') == 2
    assert text.index("Oggi") < text.index("Ieri")
    assert "Registrazioni spuntate come fatte" not in text


# --- tessere di Da fare ---

def test_todo_tiles_count_today_week_and_processing(recordings, db, settings):
    stats = catalog.todo_tiles(db, None, TODAY)
    # ogni upload mette in coda l'elaborazione: 3 in coda, nessuna in corso
    assert stats == {"todo": 3, "today": 0, "week": 2, "processing": 3, "running": False}
    assert catalog.todo_tiles(db, DEV, date(2026, 9, 23)) == {
        "todo": 2, "today": 1, "week": 1, "processing": 2, "running": False}
    text = recordings.client.get("/todo").text
    assert 'id="tiles"' in text and 'class="tile on"' in text


def test_processing_tile_counts_queued_and_running_jobs(recordings, db):
    from secondbrain.models import Job
    running_id, done_id = capture_by(db, CID).id, capture_by(db, "cap_20260810_070000").id
    jobs = {j.capture_id: j for j in db.query(Job)}   # capture_by svuota la sessione: prima gli id
    jobs[running_id].status = "running"
    jobs[done_id].status = "done"
    db.commit()
    tiles = catalog.todo_tiles(db, None, TODAY)
    assert (tiles["processing"], tiles["running"]) == (2, True)
    assert 'class="led run"' in recordings.client.get("/todo").text.split('id="tiles"')[1].split("</div>\n</div>")[0]


def test_checking_on_todo_refreshes_tiles_out_of_band(recordings, db):
    cap = capture_by(db, CID)
    headers = htmx(recordings) | {"HX-Current-URL": "http://test/todo"}
    r = recordings.client.post(f"/captures/{cap.id}/done", headers=headers)
    assert 'id="tiles"' in r.text and 'id="mhead-count"' in r.text
    headers = htmx(recordings) | {"HX-Current-URL": "http://test/browse/2026/09/23"}
    r = recordings.client.post(f"/captures/{cap.id}/undone", headers=headers)
    assert 'id="tiles"' not in r.text


# --- albero ---

def test_tree_opens_the_latest_year_by_default_with_lists(recordings):
    text = recordings.client.get("/todo").text
    tree = text.split('id="tree"')[1].split("</aside>")[0]
    assert 'href="/todo"' in tree and 'href="/done"' in tree
    assert 'href="/browse/2026/09"' in tree and 'href="/browse/2026/08"' in tree
    assert 'class="bar"' in tree


def test_month_bars_are_relative_to_the_fullest_month(recordings):
    tree = recordings.client.get("/todo").text.split('id="tree"')[1]
    assert 'style="width:100%"' in tree and 'style="width:50%"' in tree


# --- navigazione del telefono ---

def test_mobile_tabbar_and_more_sheet(recordings):
    text = recordings.client.get("/todo").text
    bar = text.split('class="tabbar"')[1].split("</nav>")[0]
    for href in ('href="/todo', 'href="/browse', 'href="/new"', 'href="/search"'):
        assert href in bar, href
    assert 'class="on"' in bar.split('href="/todo')[0].rsplit("<a", 1)[1]
    assert 'class="sheet"' in text and 'viewport-fit=cover' in text


def test_device_filter_left_the_header(recordings):
    text = recordings.client.get("/todo").text
    header = text.split('<header class="top">')[1].split("</header>")[0]
    assert "<select" not in header
    assert 'class="device-filter"' in text


def test_detail_has_a_panel_head(recordings, db):
    cap = capture_by(db, CID)
    text = recordings.client.get(f"/captures/{cap.id}").text
    assert 'class="detail-head"' in text and 'aria-label="Chiudi"' in text
