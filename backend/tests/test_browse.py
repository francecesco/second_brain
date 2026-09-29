import uuid

import pytest

from tests.helpers import DEV2, NOW, capture_by


def get(ui, url, **kw):
    return ui.client.get(url, **kw)


def test_requires_login(client):
    r = client.get("/browse", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")


def test_root_lists_years(recordings):
    r = get(recordings, "/browse")
    assert r.status_code == 200
    assert 'href="/browse/2026"' in r.text and '<span class="count">3</span>' in r.text


def test_year_lists_months(recordings):
    r = get(recordings, "/browse/2026")
    assert "agosto" in r.text and "settembre" in r.text
    assert 'href="/browse/2026/09"' in r.text


def test_month_lists_days(recordings):
    r = get(recordings, "/browse/2026/09")
    assert 'href="/browse/2026/09/23"' in r.text and '<span class="count">2</span>' in r.text


def test_day_lists_recordings_in_time_order(recordings):
    text = get(recordings, "/browse/2026/09/23").text
    assert text.index("10:00:00") < text.index("21:15:30")
    assert "Nota della sera, 21:15" in text
    assert "senza titolo" not in text


def test_month_grid_uses_weekday_labels(recordings):
    # 2026-09-23 è mercoledì.
    text = get(recordings, "/browse/2026/09").text
    assert "Mercoledì 23" in text


def test_day_tree_uses_short_weekday_labels(recordings):
    text = get(recordings, "/browse/2026/09/23").text
    assert "mer 23" in text


def test_day_breadcrumb_uses_weekday_label(recordings):
    text = get(recordings, "/browse/2026/09/23").text
    assert '<nav class="crumbs">' in text and "Mercoledì 23" in text.split('<nav class="crumbs">')[1].split("</nav>")[0]


def test_title_shown_instead_of_default_once_set(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    r = recordings.client.post(f"/captures/{cap.id}/title", data={"title": "Idea"},
                               headers={"X-CSRF-Token": recordings.csrf, "HX-Request": "true"})
    assert "Idea" in r.text and "Nota della sera, 21:15" not in r.text


def test_detail_shows_default_title_as_placeholder(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    text = get(recordings, f"/captures/{cap.id}").text
    assert 'placeholder="Nota della sera, 21:15"' in text
    assert 'value="Nota della sera, 21:15"' not in text


def test_nav_has_accessible_icons(recordings):
    text = get(recordings, "/browse").text
    assert 'aria-label="Cestino"' in text
    assert '<svg' in text and 'class="icon"' in text


def test_device_filter(recordings):
    text = get(recordings, f"/browse/2026/09/23?device={DEV2}").text
    assert "10:00:00" in text and "21:15:30" not in text
    assert f'href="/browse/2026/09?device={DEV2}"' in text
    both = get(recordings, "/browse/2026/09/23?device=../x").text
    assert "10:00:00" in both and "21:15:30" in both


def test_trashed_recordings_are_hidden(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    cap.trashed_at = NOW
    db.commit()
    assert "21:15:30" not in get(recordings, "/browse/2026/09/23").text
    assert '<span class="count">2</span>' in get(recordings, "/browse").text


@pytest.mark.parametrize("url,status", [("/browse/2026/02/30", 404), ("/browse/2026/13", 422)])
def test_invalid_dates(recordings, url, status):
    assert get(recordings, url).status_code == status


def test_detail_lists_related_files(recordings, db, settings):
    cap = capture_by(db, "cap_20260923_191530")
    (settings.archive_dir / cap.rel_path).with_suffix(".transcript.md").write_text("ciao")
    r = get(recordings, f"/captures/{cap.id}")
    assert r.status_code == 200
    for name in ["211530_70041dd8263c.json", "211530_70041dd8263c.transcript.md",
                 "211530_70041dd8263c.wav"]:
        assert name in r.text
    assert f'src="/captures/{cap.id}/audio"' in r.text


def test_audio_supports_range(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    r = get(recordings, f"/captures/{cap.id}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["content-type"].startswith("audio/wav")


def test_file_download(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    r = get(recordings, f"/captures/{cap.id}/files/211530_70041dd8263c.json")
    assert r.status_code == 200 and r.json()["capture_id"] == "cap_20260923_191530"
    assert "attachment" in r.headers["content-disposition"]


@pytest.mark.parametrize("name", ["..%2F..%2F..%2Fetc%2Fpasswd", "100000_aabbccddeeff.wav", "x.json"])
def test_file_route_serves_only_related_files(recordings, db, name):
    cap = capture_by(db, "cap_20260923_191530")
    assert get(recordings, f"/captures/{cap.id}/files/{name}").status_code == 404


def test_audio_missing_on_disk_is_404(recordings, db, settings):
    cap = capture_by(db, "cap_20260923_191530")
    (settings.archive_dir / cap.rel_path).unlink()
    assert get(recordings, f"/captures/{cap.id}/audio").status_code == 404


def test_unknown_capture(recordings):
    assert get(recordings, f"/captures/{uuid.uuid4()}").status_code == 404
