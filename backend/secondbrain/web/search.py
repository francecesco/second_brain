"""Pagina di ricerca: `/search?q=parole&tag=lavoro` (spec AI §10) e vista per tag `/tags`."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import search as fulltext
from .. import settings_store as store
from ..models import WebSession
from ..notefile import normalize_tags
from .context import page_context
from .deps import get_db, require_login
from .templating import templates

router = APIRouter()


@router.get("/search")
def search_page(request: Request, q: str = "", tag: str = "", db: Session = Depends(get_db),
                session: WebSession = Depends(require_login)):
    q = q.strip()[:fulltext.MAX_QUERY_LEN]
    clean = normalize_tags([tag])
    the_tag = clean[0] if clean else None
    hits = fulltext.search(db, q, the_tag, store.get_language(db))
    return templates.TemplateResponse(request, "search.html", page_context(request, db, session) | {
        "crumbs": [("Ricerca", None)], "q": q, "tag": the_tag, "hits": hits})


@router.get("/tags")
def tags_page(request: Request, db: Session = Depends(get_db),
              session: WebSession = Depends(require_login)):
    """Una tessera per tag, con il numero di note: porta alla ricerca filtrata per quel tag."""
    items = [(tag, n, f"/search?tag={quote(tag)}") for tag, n in fulltext.tag_counts(db)]
    return templates.TemplateResponse(request, "grid.html", page_context(request, db, session) | {
        "crumbs": [("Tag", None)], "items": items,
        "empty": "Nessun tag: le note vengono taggate dall'elaborazione AI."})
