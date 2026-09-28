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
MAX_GLOBAL_FAILURES = 100  # oltre questo blocca tutti: regge anche se qualcuno falsifica l'IP
FAILURE_WINDOW = timedelta(minutes=15)
MIN_PASSWORD_LEN = 10
MAX_PASSWORD_LEN = 1024  # oltre non si spreca argon2: è comunque una password sbagliata
TOKEN_BYTES = 32
USER_ID = 1
IP_MAX_LEN = 64  # come la colonna login_attempts.ip
# Chiave arbitraria dell'advisory lock di Postgres che serializza login(): un solo
# controllo password alla volta, altrimenti richieste in parallelo potrebbero eseguire
# più hash argon2 insieme e superare MAX_FAILURES prima che i tentativi si vedano a vicenda.
LOGIN_LOCK_KEY = 0x5EC0B4A1

_hasher = PasswordHasher()


class NotAuthenticated(Exception):
    """Serve il login."""


class LockedOut(Exception):
    """Troppi tentativi falliti di recente."""


class CsrfError(Exception):
    """Token CSRF mancante o diverso da quello della sessione."""


class BadPassword(ValueError):
    """Password non accettabile."""


def clear_lockouts(s: Session) -> int:
    """Rimuove i tentativi falliti registrati, sbloccando ogni client. Ritorna quanti."""
    result = s.execute(delete(LoginAttempt).where(LoginAttempt.ok.is_(False)))
    return result.rowcount


def set_password(s: Session, password: str, now: datetime) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise BadPassword(f"password troppo corta: almeno {MIN_PASSWORD_LEN} caratteri")
    if len(password) > MAX_PASSWORD_LEN:
        raise BadPassword(f"password troppo lunga: al massimo {MAX_PASSWORD_LEN} caratteri")
    hashed = _hasher.hash(password)
    user = s.get(User, USER_ID)
    if user is None:
        s.add(User(id=USER_ID, password_hash=hashed, updated_at=now))
    else:
        user.password_hash = hashed
        user.updated_at = now
    s.execute(delete(WebSession))  # chi era entrato con la vecchia password esce
    clear_lockouts(s)  # niente blocchi residui dopo un cambio password
    s.flush()


def _recent_failures(s: Session, now: datetime, ip: str | None) -> int:
    ip_match = LoginAttempt.ip.is_(None) if ip is None else LoginAttempt.ip == ip
    return s.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.ok.is_(False), LoginAttempt.at > now - FAILURE_WINDOW, ip_match))


def _recent_global_failures(s: Session, now: datetime) -> int:
    return s.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.ok.is_(False), LoginAttempt.at > now - FAILURE_WINDOW))


def _verify(user: User | None, password: str) -> bool:
    if user is None or len(password) > MAX_PASSWORD_LEN:
        return False
    try:
        return _hasher.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def login(s: Session, password: str, now: datetime, ip: str | None) -> WebSession:
    """Fa commit: il tentativo fallito deve restare registrato anche quando si solleva.

    Il blocco è per client (`ip`, tipicamente Cf-Connecting-Ip o l'IP della connessione:
    la scelta è di chi chiama, vedi `web.login._client_ip`) più un tetto generale che
    scatta se i tentativi falliti sono tanti da qualunque provenienza. L'intera funzione
    gira sotto un advisory lock di Postgres a livello di transazione, che la serializza
    fra richieste concorrenti: il lock si rilascia da solo al commit o al rollback.
    """
    s.execute(select(func.pg_advisory_xact_lock(LOGIN_LOCK_KEY)))
    ip = ip[:IP_MAX_LEN] if ip else None
    if (_recent_failures(s, now, ip) >= MAX_FAILURES
            or _recent_global_failures(s, now) >= MAX_GLOBAL_FAILURES):
        s.rollback()  # libera subito il lock: senza commit resterebbe preso da questa sessione
        raise LockedOut
    ok = _verify(s.get(User, USER_ID), password)
    s.add(LoginAttempt(at=now, ok=ok, ip=ip))
    if not ok:
        s.commit()
        raise NotAuthenticated
    clear_lockouts(s)
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
