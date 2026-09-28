import io
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from secondbrain.catalog import make_sessionmaker
from secondbrain.cli import main
from secondbrain.models import LoginAttempt, WebSession
from secondbrain.web.auth import (MAX_FAILURES, MAX_GLOBAL_FAILURES, MAX_PASSWORD_LEN,
                                  SESSION_LIFETIME, BadPassword, LockedOut, NotAuthenticated,
                                  current_session, login, set_password)
from secondbrain.web.templating import format_duration, format_size
from tests.helpers import NOW, PASSWORD, TEST_DB


@pytest.fixture
def user(db):
    set_password(db, PASSWORD, NOW)
    db.commit()


def failures(db):
    return db.scalar(select(func.count()).select_from(LoginAttempt)
                     .where(LoginAttempt.ok.is_(False)))


def test_password_minimum_length(db):
    with pytest.raises(BadPassword):
        set_password(db, "corta", NOW)


def test_login_ok_and_wrong(db, user):
    ws = login(db, PASSWORD, NOW, "1.2.3.4")
    assert current_session(db, ws.id, NOW) is ws
    assert current_session(db, ws.id, NOW + SESSION_LIFETIME) is None
    with pytest.raises(NotAuthenticated):
        login(db, "sbagliata", NOW, None)
    assert failures(db) == 1


def test_login_without_any_user(db):
    with pytest.raises(NotAuthenticated):
        login(db, PASSWORD, NOW, None)


def test_password_maximum_length_in_set_password(db):
    with pytest.raises(BadPassword):
        set_password(db, "x" * (MAX_PASSWORD_LEN + 1), NOW)


def test_password_maximum_length_in_login(db, user):
    with pytest.raises(NotAuthenticated):
        login(db, "x" * (MAX_PASSWORD_LEN + 1), NOW, None)
    assert failures(db) == 1


def test_lockout_after_five_failures(db, user):
    for _ in range(5):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, None)
    with pytest.raises(LockedOut):
        login(db, PASSWORD, NOW, None)  # bloccato anche con la password giusta
    assert login(db, PASSWORD, NOW + timedelta(minutes=16), None)


def test_lockout_is_scoped_per_client(db, user):
    for _ in range(MAX_FAILURES):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, "1.1.1.1")
    with pytest.raises(LockedOut):
        login(db, PASSWORD, NOW, "1.1.1.1")
    assert login(db, PASSWORD, NOW, "2.2.2.2")  # altro client, non bloccato


def test_global_backstop_blocks_everyone(db, user):
    # Sotto la soglia per client (una sola richiesta a testa) ma sopra il tetto globale.
    for i in range(MAX_GLOBAL_FAILURES):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, f"ip-{i}")
    with pytest.raises(LockedOut):
        login(db, PASSWORD, NOW, "ip-mai-vista-prima")


def test_set_password_clears_lockouts(db, user):
    for i in range(MAX_GLOBAL_FAILURES):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, f"ip-{i}")
    set_password(db, PASSWORD, NOW)
    db.commit()
    assert login(db, PASSWORD, NOW, "ip-nuova")


def test_cli_unlock_clears_lockouts(monkeypatch, db, user):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    for i in range(MAX_GLOBAL_FAILURES):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, f"ip-{i}")
    assert main(["unlock"]) == 0
    assert login(db, PASSWORD, NOW, "ip-nuova")


def test_login_is_serialized_under_concurrency(engine, user):
    """10 tentativi sbagliati in parallelo, ciascuno con una sessione propria: il lock
    serializza check-verifica-registrazione, quindi non più di MAX_FAILURES arrivano a
    eseguire argon2 e a essere registrati prima che scatti il blocco."""
    sessionmaker = make_sessionmaker(engine)
    outcomes = []
    lock = threading.Lock()

    def attempt():
        with sessionmaker() as s:
            try:
                login(s, "sbagliata", NOW, "3.3.3.3")
                outcome = "ok"
            except NotAuthenticated:
                outcome = "NotAuthenticated"
            except LockedOut:
                outcome = "LockedOut"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("NotAuthenticated") == MAX_FAILURES
    assert outcomes.count("LockedOut") == 10 - MAX_FAILURES
    with sessionmaker() as s:
        assert failures(s) == MAX_FAILURES


def test_success_clears_failures(db, user):
    for _ in range(4):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, None)
    login(db, PASSWORD, NOW, None)
    assert failures(db) == 0


def test_new_password_closes_open_sessions(db, user):
    ws = login(db, PASSWORD, NOW, None)
    set_password(db, "un'altra-password-lunga", NOW)
    db.commit()
    db.expire_all()
    assert current_session(db, ws.id, NOW) is None


def test_login_page(client):
    r = client.get("/login")
    assert r.status_code == 200 and 'name="password"' in r.text


def test_wrong_password_page(client, user):
    r = client.post("/login", data={"password": "no"})
    assert r.status_code == 401 and "Password errata" in r.text


def test_login_sets_a_safe_cookie(client, user):
    r = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/browse"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie


def test_cookie_is_secure_behind_the_tunnel(client, user):
    r = client.post("/login", data={"password": PASSWORD},
                    headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


def test_lockout_page(client, user):
    for _ in range(5):
        client.post("/login", data={"password": "no"})
    r = client.post("/login", data={"password": PASSWORD})
    assert r.status_code == 429 and "Troppi tentativi" in r.text


def test_pages_require_login(client, user):
    r = client.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")
    r = client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert (r.status_code, r.headers["hx-redirect"]) == (401, "/login")
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    r = client.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/browse")


def test_logout_requires_csrf(client, user, db):
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert client.post("/logout", follow_redirects=False).status_code == 403
    ws = db.scalars(select(WebSession)).one()
    r = client.post("/logout", data={"csrf": ws.csrf_token}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")
    db.expire_all()
    assert db.scalars(select(WebSession)).all() == []


def test_csrf_rejects_non_ascii_token(client, user):
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    r = client.post("/logout", data={"csrf": "à" * 10}, follow_redirects=False)
    assert r.status_code == 403


def test_cli_set_password(monkeypatch, db, capsys):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert main(["set-password", "--stdin"]) == 0
    assert login(db, PASSWORD, NOW, None)
    monkeypatch.setattr("sys.stdin", io.StringIO("corta\n"))
    assert main(["set-password", "--stdin"]) == 1


def test_cli_set_password_stdin_strips_crlf(monkeypatch, db):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\r\n"))
    assert main(["set-password", "--stdin"]) == 0
    assert login(db, PASSWORD, NOW, None)


@pytest.mark.parametrize("seconds,text", [(0, "0:00"), (59.6, "1:00"), (125, "2:05"), (600, "10:00")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize("size,text", [(10, "1 KB"), (32044, "31 KB"), (int(19.4 * 1024 * 1024), "19,4 MB")])
def test_format_size(size, text):
    assert format_size(size) == text
