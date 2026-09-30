from sqlalchemy import update

from secondbrain import jobs
from secondbrain import settings_store as store
from secondbrain.models import Capture, Job
from tests.ai_fakes import configure_providers, processed
from tests.helpers import NOW, capture_by, htmx

CID = "cap_20260923_191530"


def cap_id(db):
    return capture_by(db, CID).id


def head(ui, capture_id):
    return ui.client.get(f"/captures/{capture_id}/head")


def test_queued_row_shows_the_clock_and_polls(recordings, db):
    text = head(recordings, cap_id(db)).text
    assert 'aria-label="In coda"' in text
    assert f'hx-get="/captures/{cap_id(db)}/head"' in text and 'hx-trigger="every 5s"' in text
    assert 'aria-label="In coda"' in recordings.client.get("/browse/2026/09/23").text


def test_running_and_failed_rows(recordings, db):
    running = jobs.claim(db, NOW).capture_id
    assert 'aria-label="In elaborazione"' in head(recordings, running).text
    jobs.fail(db, running, NOW, "groq: HTTP 400: audio non valido", permanent=True)
    db.commit()
    failed = head(recordings, running).text
    assert 'aria-label="Elaborazione non riuscita"' in failed and "hx-trigger" not in failed


def test_processed_row_shows_the_auto_title_and_three_tags(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id, tags=("lavoro", "casa", "telefonate", "extra"))
    text = head(recordings, capture_id).text
    assert "Chiamare Marco" in text and "Nota della sera" not in text
    assert "telefonate" in text and "extra" not in text
    assert "hx-trigger" not in text and 'aria-label="In coda"' not in text
    # I chip vanno in un contenitore dedicato, separato dal titolo, cosi possono
    # andare a capo su una riga propria senza sforare (bug del layout mobile).
    head_start = text.index(f'id="head-{capture_id}"')
    title_end = text.index("</span>", text.index('class="title"', head_start))
    tags_start = text.index('<span class="tags">', head_start)
    assert tags_start > title_end, "i tag devono stare dopo il titolo, in un contenitore separato"
    assert text.count('<span class="tags">') == 1
    assert '<span class="tags"><span class="tag">lavoro</span>' in text
    assert f'id="head-{capture_id}"' in text


def test_manual_title_wins(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    r = recordings.client.post(f"/captures/{capture_id}/title", data={"title": "Mio titolo"},
                               headers=htmx(recordings))
    assert "Mio titolo" in r.text and "Chiamare Marco" not in r.text


def test_head_of_a_trashed_note_stops_polling(recordings, db):
    capture_id = cap_id(db)
    recordings.client.post(f"/captures/{capture_id}/trash", headers=htmx(recordings))
    assert head(recordings, capture_id).status_code == 286


def test_detail_shows_summary_tags_transcript_and_provenance(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    text = recordings.client.get(f"/captures/{capture_id}").text
    assert "Preventivo del tetto." in text and "Devo chiamare Marco" in text
    assert 'href="/search?tag=lavoro"' in text
    assert "Elaborata con groq · whisper-large-v3-turbo · 28/09/2026 14:00:00" in text
    assert 'placeholder="Chiamare Marco"' in text
    assert f"{capture_by(db, CID).rel_path.rsplit('/', 1)[1][:-4]}.md" in text


def test_detail_before_processing(recordings, db):
    text = recordings.client.get(f"/captures/{cap_id(db)}").text
    assert "Trascrizione non ancora disponibile." in text


def test_edit_summary_inline(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    form = recordings.client.get(f"/captures/{capture_id}/field/summary?edit=1").text
    assert '<textarea name="value"' in form and "Preventivo del tetto." in form
    r = recordings.client.post(f"/captures/{capture_id}/field/summary",
                               data={"value": "Riassunto mio"}, headers=htmx(recordings))
    assert r.status_code == 200
    assert "Riassunto mio" in r.text and 'aria-label="Corretto a mano"' in r.text
    assert 'hx-swap-oob="outerHTML"' in r.text
    db.expire_all()
    assert db.get(Capture, capture_id).edited == ["summary"]


def test_edit_tags_and_transcript(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    c = recordings.client
    r = c.post(f"/captures/{capture_id}/field/tags", data={"value": "Idee, #casa"},
               headers=htmx(recordings))
    assert 'href="/search?tag=idee"' in r.text and '<span class="tag">idee</span>' in r.text
    c.post(f"/captures/{capture_id}/field/transcript", data={"value": "Testo giusto"},
           headers=htmx(recordings))
    db.expire_all()
    cap = db.get(Capture, capture_id)
    assert (cap.tags, cap.transcript, cap.edited) == (["idee", "casa"], "Testo giusto",
                                                      ["transcript", "tags"])


def test_new_actions_require_csrf(recordings, db):
    capture_id = cap_id(db)
    c = recordings.client
    assert c.post(f"/captures/{capture_id}/field/summary", data={"value": "x"}).status_code == 403
    assert c.post(f"/captures/{capture_id}/reprocess").status_code == 403


def test_unknown_field_and_trashed_note(recordings, db):
    capture_id = cap_id(db)
    c = recordings.client
    assert c.get(f"/captures/{capture_id}/field/title").status_code == 404
    c.post(f"/captures/{capture_id}/trash", headers=htmx(recordings))
    r = c.post(f"/captures/{capture_id}/field/summary", data={"value": "x"}, headers=htmx(recordings))
    assert r.status_code == 409


def test_edit_summary_before_processing_is_refused(recordings, db):
    """Ruling P14: `library.edit_ai_field` alza `NotProcessed` (non `ValueError`) sul
    riassunto/tag di una nota mai elaborata; la route lo mappa su 409."""
    capture_id = cap_id(db)
    r = recordings.client.post(f"/captures/{capture_id}/field/summary", data={"value": "x"},
                               headers=htmx(recordings))
    assert r.status_code == 409


def test_reprocess_warns_about_edited_fields_and_requeues(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id, edited=("tags",))
    detail = recordings.client.get(f"/captures/{capture_id}").text
    assert ('hx-confirm="Rielaborare la nota? I campi corretti a mano (tag) resteranno '
            'invariati."') in detail
    r = recordings.client.post(f"/captures/{capture_id}/reprocess", headers=htmx(recordings))
    assert r.status_code == 200
    assert 'aria-label="In coda"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
    db.expire_all()
    assert db.get(Capture, capture_id).job.status == jobs.QUEUED


def test_reprocess_while_running_is_refused(recordings, db):
    capture_id = cap_id(db)
    db.execute(update(Job).where(Job.capture_id != capture_id).values(status=jobs.DONE))
    db.commit()
    jobs.claim(db, NOW)
    assert "Rielabora" not in recordings.client.get(f"/captures/{capture_id}").text
    r = recordings.client.post(f"/captures/{capture_id}/reprocess", headers=htmx(recordings))
    assert r.status_code == 409 and "in elaborazione" in r.text


def test_failed_note_shows_the_error(recordings, db):
    capture_id = cap_id(db)
    jobs.fail(db, capture_id, NOW, "groq: HTTP 400: audio non valido", permanent=True)
    db.commit()
    text = recordings.client.get(f"/captures/{capture_id}").text
    assert "Elaborazione non riuscita: groq: HTTP 400: audio non valido" in text
    assert 'aria-label="Rielabora"' in text


def test_finder_says_why_notes_are_waiting(recordings, db, settings):
    c = recordings.client
    assert "Nessun provider AI con una chiave" in c.get("/browse").text
    configure_providers(db, store.SecretBox(settings.settings_key), NOW)
    assert "Nessun provider AI" not in c.get("/browse").text
    store.set_paused(db, True)
    db.commit()
    assert "Elaborazione AI in pausa" in c.get("/browse").text


def test_finder_notice_when_key_undecryptable_with_current_settings_key(recordings, db, settings):
    """Ruling P3: l'avviso usa `provider_configs` (decifra), non solo "una chiave salvata?":
    una chiave cifrata con un'altra SETTINGS_KEY deve comunque far comparire l'avviso."""
    other_box = store.SecretBox(store.generate_key())
    configure_providers(db, other_box, NOW)
    assert "Nessun provider AI con una chiave" in recordings.client.get("/browse").text


def test_queued_note_waiting_on_backoff_shows_the_last_error(recordings, db):
    """Final review 10: in attesa del prossimo tentativo si vede perché l'ultimo è fallito,
    con la chiave nascosta e l'HTML escapato."""
    capture_id = cap_id(db)
    jobs.claim(db, NOW)
    jobs.fail(db, capture_id, NOW, "groq: HTTP 503 <b>giù</b> gsk_test_abcdefgh0123456789",
              permanent=False)
    db.commit()
    text = recordings.client.get(f"/captures/{capture_id}").text
    assert "Ultimo tentativo non riuscito" in text
    assert "groq: HTTP 503 &lt;b&gt;giù&lt;/b&gt;" in text
    assert "gsk_test_abcdefgh" not in text


def test_finder_notice_without_a_text_model(recordings, db, settings):
    """Final review 5: solo OpenAI, senza modello di testo: l'arricchimento non parte mai."""
    configure_providers(db, store.SecretBox(settings.settings_key), NOW, names=("openai",))
    assert "Nessun provider AI ha un modello di testo" in recordings.client.get("/browse").text
