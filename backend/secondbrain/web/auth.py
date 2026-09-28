"""Login a utente singolo, sessioni in DB, blocco dopo tentativi falliti (spec §7)."""
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import LoginAttempt, User, WebSession

COOKIE_NAME = "sb_session"
SESSION_LIFETIME = timedelta(days=30)
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
MIN_PASSWORD_LEN = 10
TOKEN_BYTES = 32
USER_ID = 1

_hasher = PasswordHasher()


class NotAuthenticated(Exception):
    """Serve il login."""


class LockedOut(Exception):
    """Troppi tentativi falliti di recente."""


class CsrfError(Exception):
    """Token CSRF mancante o diverso da quello della sessione."""


class BadPassword(ValueError):
    """Password non accettabile."""


def set_password(s: Session, password: str, now: datetime) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise BadPassword(f"password troppo corta: almeno {MIN_PASSWORD_LEN} caratteri")
    hashed = _hasher.hash(password)
    user = s.get(User, USER_ID)
    if user is None:
        s.add(User(id=USER_ID, password_hash=hashed, updated_at=now))
    else:
        user.password_hash = hashed
        user.updated_at = now
    s.execute(delete(WebSession))  # chi era entrato con la vecchia password esce
    s.flush()


def _recent_failures(s: Session, now: datetime) -> int:
    return s.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.ok.is_(False), LoginAttempt.at > now - FAILURE_WINDOW))


def _verify(user: User | None, password: str) -> bool:
    if user is None:
        return False
    try:
        return _hasher.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def login(s: Session, password: str, now: datetime, ip: str | None) -> WebSession:
    """Fa commit: il tentativo fallito deve restare registrato anche quando si solleva."""
    if _recent_failures(s, now) >= MAX_FAILURES:
        raise LockedOut
    ok = _verify(s.get(User, USER_ID), password)
    s.add(LoginAttempt(at=now, ok=ok, ip=ip[:64] if ip else None))
    if not ok:
        s.commit()
        raise NotAuthenticated
    s.execute(delete(LoginAttempt).where(LoginAttempt.ok.is_(False)))
    s.execute(delete(WebSession).where(WebSession.expires_at <= now))
    session = WebSession(id=secrets.token_urlsafe(TOKEN_BYTES),
                         csrf_token=secrets.token_urlsafe(TOKEN_BYTES),
                         created_at=now, expires_at=now + SESSION_LIFETIME)
    s.add(session)
    s.commit()
    return session


def current_session(s: Session, sid: str, now: datetime) -> WebSession | None:
    session = s.get(WebSession, sid)
    if session is None or session.expires_at <= now:
        return None
    return session


def logout(s: Session, sid: str) -> None:
    s.execute(delete(WebSession).where(WebSession.id == sid))
    s.commit()
