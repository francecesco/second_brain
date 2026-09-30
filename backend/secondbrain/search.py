"""Ricerca full-text sulle note (spec §10).

`search_vector` si ricalcola dal codice a ogni scrittura dei campi con un'unica
espressione: peso A il titolo mostrato (manuale o automatico), B tag e riassunto,
C la trascrizione, nella configurazione di testo della lingua della nota.
"""
from dataclasses import dataclass

from markupsafe import Markup, escape
from sqlalchemy import cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session, selectinload

from .languages import text_search_config
from .models import Capture

SEARCH_LIMIT = 50
MAX_QUERY_LEN = 200
EXCERPT_CHARS = 240  # estratto senza parole da evidenziare (ricerca solo per tag)
EXCERPT_SEPARATOR = " — "
# Segnaposto di ts_headline: caratteri Unicode privati, che non compaiono nel testo delle
# note; l'estratto si fa l'escape HTML e solo dopo diventano <mark>.
MARK_START = ""
MARK_END = ""
HEADLINE_MAX_WORDS = 35
HEADLINE_MIN_WORDS = 15
HEADLINE_FRAGMENTS = 2
HEADLINE_OPTIONS = (f"StartSel={MARK_START}, StopSel={MARK_END}, MaxWords={HEADLINE_MAX_WORDS}, "
                    f"MinWords={HEADLINE_MIN_WORDS}, MaxFragments={HEADLINE_FRAGMENTS}")

_REFRESH = text("""
UPDATE captures SET search_vector =
       setweight(to_tsvector(CAST(:cfg AS regconfig), coalesce(title, title_auto, '')), 'A')
    || setweight(to_tsvector(CAST(:cfg AS regconfig),
                             array_to_string(tags, ' ') || ' ' || coalesce(summary, '')), 'B')
    || setweight(to_tsvector(CAST(:cfg AS regconfig), coalesce(transcript, '')), 'C')
WHERE id = :id
""")


def refresh_search_vector(s: Session, capture: Capture) -> None:
    """Da chiamare dopo ogni modifica di titolo o campi AI, prima del commit."""
    s.flush()
    s.execute(_REFRESH, {"cfg": text_search_config(capture.language), "id": capture.id})


@dataclass(frozen=True)
class SearchHit:
    capture: Capture
    excerpt: Markup


def highlight(raw: str | None) -> Markup:
    """Estratto sicuro: prima l'escape di tutto il testo, poi i segnaposto diventano <mark>."""
    safe = str(escape(raw or ""))
    return Markup(safe.replace(MARK_START, "<mark>").replace(MARK_END, "</mark>"))


def search(s: Session, q: str, tag: str | None, language: str,
           limit: int = SEARCH_LIMIT) -> list[SearchHit]:
    """Note fuori dal cestino per parole (`websearch_to_tsquery`) e/o tag esatto.

    Ordine: pertinenza, poi data più recente; senza parole, solo data più recente.
    """
    q = q.strip()[:MAX_QUERY_LEN]
    if not q and not tag:
        return []
    cfg = cast(literal(text_search_config(language)), REGCONFIG)
    body = func.concat_ws(EXCERPT_SEPARATOR, Capture.summary, Capture.transcript)
    stmt = (select(Capture).where(Capture.trashed_at.is_(None))
            .options(selectinload(Capture.job)))
    if tag:
        stmt = stmt.where(Capture.tags.contains([tag]))
    if q:
        query = func.websearch_to_tsquery(cfg, q)
        stmt = (stmt.add_columns(func.ts_headline(cfg, body, query, HEADLINE_OPTIONS))
                .where(Capture.search_vector.bool_op("@@")(query))
                .order_by(func.ts_rank(Capture.search_vector, query).desc(),
                          Capture.recorded_at.desc()))
    else:
        stmt = (stmt.add_columns(func.left(body, EXCERPT_CHARS))
                .order_by(Capture.recorded_at.desc()))
    return [SearchHit(capture, highlight(raw)) for capture, raw in s.execute(stmt.limit(limit))]
