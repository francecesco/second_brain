"""Dipendenze FastAPI delle pagine: sessione DB, login, CSRF."""
import secrets
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..models import WebSession
from .auth import COOKIE_NAME, CsrfError, NotAuthenticated, current_session


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as s:
        yield s


def require_login(request: Request, db: Session = Depends(get_db)) -> WebSession:
    sid = request.cookies.get(COOKIE_NAME)
    session = current_session(db, sid, request.app.state.clock()) if sid else None
    if session is None:
        raise NotAuthenticated
    return session


async def require_csrf(request: Request,
                       session: WebSession = Depends(require_login)) -> WebSession:
    token = request.headers.get("x-csrf-token")
    if token is None:
        token = (await request.form()).get("csrf")
    if not isinstance(token, str) or not secrets.compare_digest(
            token.encode(), session.csrf_token.encode()):
        # compare_digest rifiuta str non ASCII (TypeError): confrontiamo sempre i byte.
        raise CsrfError
    return session
