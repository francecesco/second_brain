from datetime import date

import pytest
from markupsafe import Markup

from secondbrain import library, search
from secondbrain.archive import Archive
from secondbrain.web.templating import day_month_label
from tests.ai_fakes import processed
from tests.helpers import NOW, capture_by

EVENING = "cap_20260923_191530"   # 23/09 21:15:30
MORNING = "cap_20260923_080000"   # 23/09 10:00:00
AUGUST = "cap_20260810_070000"    # 10/08 09:00:00


@pytest.fixture
def notes(recordings, db, settings):
    ids = {cid: capture_by(db, cid).id for cid in (EVENING, MORNING, AUGUST)}
    processed(db, settings, ids[EVENING], title="Chiamare Marco", summary="Preventivo.",
              tags=("casa", "lavoro"), transcript="Ho chiamato Marco per il preventivo del tetto.")
    processed(db, settings, ids[MORNING], title="Riparare il tetto", summary="Lista della spesa.",
              tags=("casa",), transcript="Pane e latte.")
    processed(db, settings, ids[AUGUST], title="Idea", summary="Un'idea per il lavoro.",
              tags=("lavori",), transcript="Scrivere la documentazione <b>subito</b> & bene.")
    return ids


def found(db, q, tag=None):
    return [hit.capture.id for hit in search.search(db, q, tag, "it")]


def test_stemming_finds_other_forms_of_the_word(notes, db):
    assert found(db, "chiamare") == [notes[EVENING]]


def test_title_weighs_more_than_transcript(notes, db):
    # a parità di parole vincerebbe la più recente (sera); il titolo pesa di più
    assert found(db, "tetto") == [notes[MORNING], notes[EVENING]]


def test_trash_is_excluded(notes, db, settings):
    library.trash_capture(db, Archive(settings.archive_dir), notes[EVENING], NOW)
    assert found(db, "tetto") == [notes[MORNING]]


def test_tag_filter_is_exact_and_combines_with_words(notes, db):
    assert found(db, "", "lavoro") == [notes[EVENING]]
    assert found(db, "", "casa") == [notes[EVENING], notes[MORNING]]  # più recente prima
    assert found(db, "spesa", "casa") == [notes[MORNING]]
    assert found(db, "spesa", "lavoro") == []


def test_nothing_to_search(notes, db):
    assert found(db, "   ") == []
    assert found(db, "il e la") == []  # solo parole vuote


def test_excerpt_highlights_the_words_and_escapes_the_rest(notes, db):
    [hit] = search.search(db, "documentazione", None, "it")
    assert "<mark>documentazione</mark>" in hit.excerpt
    assert "<b>" not in hit.excerpt and "&amp;" in hit.excerpt


def test_highlight_escapes_before_marking():
    raw = f"a <script>x</script> {search.MARK_START}parola{search.MARK_END}"
    assert search.highlight(raw) == Markup(
        "a &lt;script&gt;x&lt;/script&gt; <mark>parola</mark>")


def test_day_month_label():
    assert day_month_label(date(2026, 9, 29)) == "Martedì 29 set"


def test_search_page(notes, recordings, db):
    text = recordings.client.get("/search?q=chiamare").text
    capture_id = notes[EVENING]
    assert "Mercoledì 23 set" in text and "Chiamare Marco" in text
    assert "<mark>chiamato</mark>" in text
    assert f'href="/browse/2026/09/23?open={capture_id}#row-{capture_id}"' in text
    assert 'value="chiamare"' in text


def test_search_page_by_tag(notes, recordings):
    text = recordings.client.get("/search?tag=%23Casa").text
    assert "Chiamare Marco" in text and "Riparare il tetto" in text and "Idea" not in text
    assert 'name="tag" value="casa"' in text
    assert "Nessuna nota trovata." in recordings.client.get("/search?q=astronave").text


def test_search_requires_login(client):
    r = client.get("/search?q=x", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")


def test_header_has_search_and_menu_entry(recordings):
    text = recordings.client.get("/browse").text
    assert 'action="/search"' in text and 'aria-label="Cerca nelle note"' in text
    assert 'aria-label="Ricerca"' in text and 'class="search-open"' in text


def test_day_page_opens_the_requested_note(notes, recordings):
    capture_id = notes[EVENING]
    text = recordings.client.get(f"/browse/2026/09/23?open={capture_id}").text
    assert (f'id="detail-{capture_id}" hx-get="/captures/{capture_id}" hx-trigger="load"') in text
    assert 'hx-trigger="load"' not in recordings.client.get("/browse/2026/09/23").text
