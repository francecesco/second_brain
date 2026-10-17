"""Azioni (spec azioni §4): prompt, contesto, risposta dei provider, worker e fase `actions`."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from secondbrain import jobs, library
from secondbrain import homeassistant as ha
from secondbrain.action_context import action_context
from secondbrain.ai import base
from secondbrain.ai.gemini import ENRICH_SCHEMA
from secondbrain.cli import main
from secondbrain.ha_store import Snapshot
from secondbrain.notefile import Action
from tests.helpers import NOW, TEST_DB
from tests.test_worker import env, fresh, job_of  # noqa: F401 - fixture e utilità condivise

ROME = ZoneInfo("Europe/Rome")
WITH_ACTIONS = ("Chiamare Marco", "Preventivo.", ("marco",),
                [{"text": "Chiamare Marco", "due": "2026-09-25"},
                 {"text": "Pagare il bollo", "due": "2025-01-01"},      # anno sbagliato
                 {"text": "Scrivere a Stefano", "due": None}])


def test_prompt_asks_for_actions_with_dates_and_context():
    prompt = base.enrich_prompt("it")
    assert '"actions"' in prompt and "AAAA-MM-GG" in prompt and "quattro chiavi" in prompt
    assert "Contesto" not in prompt
    hints = base.Hints(context=("La nota è stata registrata mercoledì 23 settembre 2026 alle 21:15.",))
    assert "Contesto: La nota è stata registrata mercoledì 23" in base.enrich_prompt("it", hints)
    assert "registrata" not in base.transcribe_prompt("it", hints)


def test_parse_enrichment_reads_actions_or_their_absence():
    got = base.parse_enrichment("groq", "m", '{"title": "T", "summary": "", "tags": [], '
                                '"actions": [{"text": "Chiamare", "due": "2026-10-23"}]}', {})
    assert [(a.text, a.due) for a in got.actions] == [("Chiamare", date(2026, 10, 23))]
    assert base.parse_enrichment("groq", "m", '{"title": "T", "summary": "", "tags": []}',
                                 {}).actions is None


def test_gemini_schema_requires_actions():
    assert "actions" in ENRICH_SCHEMA["required"]
    assert ENRICH_SCHEMA["properties"]["actions"]["items"]["properties"]["due"]["nullable"] is True


def _snapshot(*events):
    reading = ha.Reading(events=tuple(events), shopping=(), sensors=(), missing=())
    return Snapshot(NOW, reading)


def test_action_context_has_the_recording_moment_and_next_events():
    recorded = datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)          # 21:15 a Roma
    assert action_context(recorded, None, ROME) == (
        "La nota è stata registrata mercoledì 23 settembre 2026 alle 21:15.",)
    events = (ha.Event("calendar.casa", "Riunione con Marco", "2026-09-25T09:30:00+02:00", "x", False),
              ha.Event("calendar.casa", "Ferie", "2026-09-28", "2026-09-29", True),
              ha.Event("calendar.casa", "Passato", "2026-09-20", "2026-09-21", True),
              ha.Event("calendar.casa", "Troppo avanti", "2026-10-20", "2026-10-21", True),
              ha.Event("calendar.casa", "Rotto", "boh", "boh", False))
    lines = action_context(recorded, _snapshot(*events), ROME)
    assert len(lines) == 2
    assert lines[1] == ("Impegni in calendario nei giorni seguenti: venerdì 25 settembre 2026 alle "
                        "09:30: Riunione con Marco; lunedì 28 settembre 2026: Ferie.")


def test_worker_saves_actions_and_drops_absurd_dates(env, db):  # noqa: F811
    env.groq.enrichments = [WITH_ACTIONS]
    env.worker.run_once()
    cap = fresh(db, env)
    assert [(a["text"], a["due"]) for a in cap.actions] == [
        ("Chiamare Marco", "2026-09-25"), ("Pagare il bollo", None), ("Scrivere a Stefano", None)]
    assert "actions:" in env.archive.read_text(cap.rel_path, ".md")
    hints = env.groq.hints[-1]
    assert hints.context and "mercoledì 23 settembre 2026 alle 21:15" in hints.context[0]


def test_retag_keeps_actions_and_actions_stage_keeps_the_rest(env, db):  # noqa: F811
    env.groq.enrichments = [WITH_ACTIONS]
    env.worker.run_once()
    before = fresh(db, env)
    jobs.enqueue_retag(db, NOW)
    db.commit()
    env.groq.enrichments = [("Altro", "Altro.", ("nuovo",), [{"text": "Altra cosa"}])]
    env.worker.run_once()
    cap = fresh(db, env)
    assert cap.tags == ["nuovo"] and cap.actions == before.actions
    assert jobs.enqueue_actions(db, NOW) == 1
    db.commit()
    env.groq.enrichments = [("Ignorato", "Ignorato.", ("ignorato",), [{"text": "Solo questa"}])]
    env.worker.run_once()
    cap = fresh(db, env)
    assert [a["text"] for a in cap.actions] == ["Solo questa"]
    assert (cap.title_auto, cap.tags) == (before.title_auto, ["nuovo"])
    assert job_of(db, env).status == jobs.DONE


def test_provider_without_actions_keeps_the_old_ones(env, db):  # noqa: F811
    env.groq.enrichments = [WITH_ACTIONS]
    env.worker.run_once()
    jobs.enqueue_actions(db, NOW)
    db.commit()
    env.groq.enrichments = [("T", "S.", ("marco",))]                   # senza la chiave
    env.worker.run_once()
    assert len(fresh(db, env).actions) == 3


def test_actions_stage_skips_done_and_edited_notes(env, db, settings):  # noqa: F811
    env.worker.run_once()
    library.set_done(db, env.archive, env.cap_id, True, NOW)
    db.commit()
    assert jobs.count_actions(db) == 0
    library.set_done(db, env.archive, env.cap_id, False, NOW)
    library.edit_actions(db, env.archive, ROME, env.cap_id, [Action("", "Mia")])
    db.commit()
    assert jobs.enqueue_actions(db, NOW) == 0


def test_cli_actions(env, db, monkeypatch, capsys):  # noqa: F811
    env.worker.run_once()
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    assert main(["actions", "--all"]) == 0
    assert "1 note" in capsys.readouterr().out
    job = job_of(db, env)
    assert (job.stage, job.priority) == (jobs.STAGE_ACTIONS, jobs.PRIORITY_LOW)
    assert main(["actions"]) != 0


def test_settings_button_queues_actions(env, db):  # noqa: F811
    env.worker.run_once()
    from sqlalchemy import select
    from secondbrain.models import WebSession
    from secondbrain.web.auth import set_password
    from tests.helpers import PASSWORD
    set_password(db, PASSWORD, NOW)
    db.commit()
    env.client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    csrf = db.scalars(select(WebSession)).one().csrf_token
    assert "Ricava le azioni delle note aperte (1)" in env.client.get("/settings").text
    r = env.client.post("/settings/actions", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?queued=1"
