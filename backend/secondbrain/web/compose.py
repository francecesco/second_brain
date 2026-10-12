"""Nota scritta dall'app (spec note scritte §3): pagina /new e salvataggio."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import settings_store as store
from .. import textnotes
from ..models import WebSession
from .context import page_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

SAVE_FAILED = "Salvataggio non riuscito (disco?): il testo è ancora qui, riprova."
SAVE_ERROR = "Salvataggio non riuscito: il testo è ancora qui, riprova."
# Il segno nell'indirizzo dice a base.html che il salvataggio è riuscito: solo allora la
# bozza nel browser si toglie (una sessione scaduta porta al login, e lì deve restare).
SAVED_URL = "/todo#salvata"
log = logging.getLogger(__name__)
router = APIRouter()


def _rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        log.exception("rollback non riuscito dopo un errore di /new")


def _page(request: Request, db: Session, session: WebSession, *, text: str = "",
          capture_id: str | None = None, error: str | None = None, status: int = 200):
    ctx = page_context(request, db, session) | {
        "crumbs": [("Nuova nota", None)], "text": text, "error": error,
        "capture_id": capture_id or textnotes.new_capture_id(request.app.state.clock()),
        "MAX_TEXT_NOTE_CHARS": textnotes.MAX_TEXT_NOTE_CHARS}
    return templates.TemplateResponse(request, "new.html", ctx, status_code=status)


@router.get("/new")
def new_page(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)):
    return _page(request, db, session)


@router.post("/new")
def save_note(request: Request, text: str = Form(""), capture_id: str = Form(""),
              db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    """Salva e torna a "Da fare". Il `capture_id` nasce con la pagina: un doppio invio
    (rete lenta, secondo tocco) ritrova la nota già creata invece di farne un'altra."""
    state = request.app.state
    try:
        textnotes.create_text_note(
            db, state.archive, text, capture_id, state.clock(), tz=state.settings.tz_archive,
            language=store.get_language(db), vault=state.vault, public_url=state.settings.public_url)
    except textnotes.TextNoteError as exc:
        _rollback(db)
        return _page(request, db, session, text=text, capture_id=capture_id, error=str(exc), status=422)
    except OSError:
        log.exception("nota scritta non salvata (disco)")
        _rollback(db)
        return _page(request, db, session, text=text, capture_id=capture_id, error=SAVE_FAILED,
                     status=500)
    except Exception:
        log.exception("nota scritta non salvata")
        _rollback(db)
        return _page(request, db, session, text=text, capture_id=capture_id, error=SAVE_ERROR,
                     status=500)
    return RedirectResponse(SAVED_URL, status_code=303)
