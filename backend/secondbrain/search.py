"""Ricerca full-text sulle note (spec §10).

`search_vector` si ricalcola dal codice a ogni scrittura dei campi con un'unica
espressione: peso A il titolo mostrato (manuale o automatico), B tag e riassunto,
C la trascrizione, nella configurazione di testo della lingua della nota.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from .languages import text_search_config
from .models import Capture

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
