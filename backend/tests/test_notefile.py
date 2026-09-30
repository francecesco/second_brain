from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain.languages import language_name, text_search_config
from secondbrain.notefile import (MAX_MODEL_LEN, MAX_PROVIDER_LEN, MAX_SUMMARY_LEN, MAX_TAG_LEN,
                                  MAX_TAGS, MAX_TITLE_LEN,
                                  EnrichmentInvalid, Note, NoteError, apply_edit,
                                  apply_enrichment, apply_transcript, parse_note,
                                  parse_tags_input, render_note, validate_enrichment)

ROME = ZoneInfo("Europe/Rome")
AT = datetime(2026, 9, 29, 10, 51, 10, tzinfo=UTC)

SPEC_NOTE = Note(
    transcript="Allora, devo ricordarmi di chiamare Marco…",
    title="Chiamare Marco per il preventivo",
    summary="Ricordarsi di chiamare Marco entro venerdì per il preventivo del tetto.",
    tags=("lavoro", "casa", "telefonate"), language="it", provider="groq",
    transcribe_model="whisper-large-v3-turbo", enrich_provider="groq",
    enrich_model="openai/gpt-oss-120b", processed_at=AT, edited=("tags",))

# L'esempio del §6 della spec; YAML mette tra apici la data per non farla leggere come timestamp.
SPEC_TEXT = """---
title: Chiamare Marco per il preventivo
summary: Ricordarsi di chiamare Marco entro venerdì per il preventivo del tetto.
tags: [lavoro, casa, telefonate]
language: it
provider: groq
models: {transcribe: whisper-large-v3-turbo, enrich: openai/gpt-oss-120b}
processed_at: '2026-09-29T12:51:10+02:00'
edited: [tags]
---

Allora, devo ricordarmi di chiamare Marco…
"""


def test_languages():
    assert text_search_config("it") == "italian"
    assert text_search_config(None) == "italian"
    assert text_search_config("xx") == "simple"
    assert language_name("en") == "inglese" and language_name("xx") == "xx"


def test_render_matches_the_spec_example():
    assert render_note(SPEC_NOTE, ROME) == SPEC_TEXT


def test_round_trip():
    assert parse_note(render_note(SPEC_NOTE, ROME)) == SPEC_NOTE


def test_after_transcription_only_there_is_no_title_summary_tags():
    note = Note(transcript="Ciao", language="it", provider="groq",
                transcribe_model="whisper-large-v3-turbo", processed_at=AT)
    text = render_note(note, ROME)
    assert "title:" not in text and "summary:" not in text and "tags:" not in text
    assert "models: {transcribe: whisper-large-v3-turbo}" in text
    assert parse_note(text) == note


def test_empty_transcript_is_only_frontmatter():
    text = render_note(Note(language="it"), ROME)
    assert text == "---\nlanguage: it\nedited: []\n---\n"
    assert parse_note(text).transcript == ""


def test_enrich_provider_is_written_only_when_different():
    other = Note(transcript="x", provider="groq", transcribe_model="w",
                 enrich_provider="gemini", enrich_model="g")
    assert "enrich_provider: gemini" in render_note(other, ROME)
    assert parse_note(render_note(other, ROME)) == other
    assert "enrich_provider" not in render_note(SPEC_NOTE, ROME)


def test_parse_accepts_hand_edits():
    text = ("---\r\ntitle:   Titolo   scritto a mano  \r\ntags: '#Lavoro'\r\n"
            "processed_at: 2026-09-29T12:51:10+02:00\r\nedited: [tags, sconosciuto]\r\n"
            "---\r\n\r\nTesto corretto.\r\n")
    note = parse_note(text)
    assert note.title == "Titolo scritto a mano"
    assert note.tags == ("lavoro",)
    assert note.processed_at == AT
    assert note.edited == ("tags",)
    assert note.transcript == "Testo corretto."


@pytest.mark.parametrize("text", [
    "senza frontmatter",
    "---\ntitle: x\n",
    "---\ntitle: [non chiusa\n---\n",
    "---\n- una\n- lista\n---\n",
    "---\ntags: {a: 1}\n---\n",
    "---\nmodels: [a]\n---\n",
    "---\ntitle: {a: 1}\n---\n",
    "---\nprocessed_at: ieri\n---\n",
])
def test_parse_errors(text):
    with pytest.raises(NoteError):
        parse_note(text)


def test_transcription_keeps_an_edited_transcript():
    base = Note(transcript="corretto a mano", edited=("transcript",))
    note = apply_transcript(base, " nuovo ", provider="gemini", model="m", language="it", at=AT)
    assert (note.transcript, note.provider, note.transcribe_model, note.processed_at) == (
        "corretto a mano", "gemini", "m", AT)
    fresh = apply_transcript(Note(), " nuovo ", provider="groq", model="w", language="it", at=AT)
    assert fresh.transcript == "nuovo" and fresh.language == "it"


def test_enrichment_keeps_edited_summary_and_tags():
    base = Note(transcript="t", summary="mio", tags=("mio",), edited=("summary", "tags"))
    note = apply_enrichment(base, "Titolo", "auto", ("auto",), provider="groq", model="m", at=AT)
    assert (note.title, note.summary, note.tags) == ("Titolo", "mio", ("mio",))
    note = apply_enrichment(Note(transcript="t"), "Titolo", "auto", ("auto",), provider="groq",
                            model="m", at=AT)
    assert (note.summary, note.tags, note.enrich_provider, note.enrich_model) == (
        "auto", ("auto",), "groq", "m")


def test_edits_mark_the_field_once():
    note = apply_edit(Note(summary="auto"), "summary", "  Mio   riassunto ")
    assert (note.summary, note.edited) == ("Mio riassunto", ("summary",))
    note = apply_edit(note, "tags", "Lavoro, #casa, , lavoro")
    note = apply_edit(note, "summary", "")
    assert (note.tags, note.summary, note.edited) == (("lavoro", "casa"), None, ("summary", "tags"))
    assert apply_edit(Note(), "transcript", " testo ").transcript == "testo"
    with pytest.raises(ValueError):
        apply_edit(Note(), "title", "x")


def test_parse_tags_input():
    assert parse_tags_input("Lavoro, #casa,  , lavoro") == ("lavoro", "casa")
    assert parse_tags_input("") == ()


def test_validate_enrichment_normalizes_and_limits():
    title, summary, tags = validate_enrichment({
        "title": "Riga uno\nriga due " + "x" * 300,
        "summary": "s" * (MAX_SUMMARY_LEN + 100),
        "tags": ["#Lavoro", " CASA ", "lavoro", "y" * 50] + [f"t{i}" for i in range(10)],
    })
    assert title.startswith("Riga uno riga due") and len(title) == MAX_TITLE_LEN
    assert len(summary) == MAX_SUMMARY_LEN
    assert tags[:3] == ("lavoro", "casa", "y" * MAX_TAG_LEN) and len(tags) == MAX_TAGS
    assert validate_enrichment({"title": "T", "summary": "", "tags": []}) == ("T", None, ())


@pytest.mark.parametrize("data", [
    "non un oggetto", ["lista"], {"summary": "s", "tags": []},
    {"title": "   ", "summary": "s", "tags": []}, {"title": "T", "summary": 3, "tags": []},
    {"title": "T", "summary": "s"}, {"title": "T", "summary": "s", "tags": "lavoro"},
    {"title": "T", "summary": "s", "tags": ["ok", 3]},
])
def test_validate_enrichment_rejects_bad_shapes(data):
    with pytest.raises(EnrichmentInvalid):
        validate_enrichment(data)


@pytest.mark.parametrize("line", [
    "language: portoghese",
    f"provider: {'p' * (MAX_PROVIDER_LEN + 1)}",
    f"enrich_provider: {'p' * (MAX_PROVIDER_LEN + 1)}",
    f"models: {{transcribe: {'m' * (MAX_MODEL_LEN + 1)}}}",
    f"models: {{enrich: {'m' * (MAX_MODEL_LEN + 1)}}}",
])
def test_parse_rejects_values_the_catalog_cannot_hold(line):
    """Final review 2: un valore scritto a mano che non sta nella colonna è un `.md`
    illeggibile, non un errore del database a metà rescan."""
    with pytest.raises(NoteError):
        parse_note(f"---\n{line}\n---\n\nTesto.\n")


def test_empty_transcript_clears_the_previous_enrichment():
    """Final review 4: "Rielabora" senza parlato non lascia titolo, riassunto e tag vecchi."""
    old = Note(transcript="vecchio", title="Vecchio", summary="vecchio", tags=("a",),
               enrich_provider="groq", enrich_model="m", edited=("tags",))
    note = apply_transcript(old, "  ", provider="groq", model="w", language="it", at=AT)
    assert (note.transcript, note.title, note.summary, note.tags) == ("", None, None, ("a",))
    assert (note.enrich_provider, note.enrich_model) == (None, None)
    kept = apply_transcript(old, "nuovo", provider="groq", model="w", language="it", at=AT)
    assert (kept.title, kept.summary) == ("Vecchio", "vecchio")
