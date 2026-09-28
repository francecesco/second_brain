"""Pagine di accesso e uscita."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..httputil import public_scheme
from ..models import WebSession
from .auth import COOKIE_NAME, SESSION_LIFETIME, LockedOut, NotAuthenticated, login, logout
from .deps import get_db, require_csrf, require_login
from .templating import templates

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, password: str = Form(""), db: Session = Depends(get_db)):
    try:
        session = login(db, password, request.app.state.clock(), _client_ip(request))
    except LockedOut:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Troppi tentativi falliti: riprova tra qualche minuto."}, status_code=429)
    except NotAuthenticated:
        return templates.TemplateResponse(request, "login.html", {"error": "Password errata."},
                                          status_code=401)
    response = RedirectResponse("/browse", status_code=303)
    response.set_cookie(COOKIE_NAME, session.id, max_age=int(SESSION_LIFETIME.total_seconds()),
                        httponly=True, samesite="lax",
                        secure=public_scheme(request) == "https", path="/")
    return response


@router.post("/logout")
def logout_submit(session: WebSession = Depends(require_csrf), db: Session = Depends(get_db)):
    logout(db, session.id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/")
def home(session: WebSession = Depends(require_login)):
    return RedirectResponse("/browse", status_code=303)
