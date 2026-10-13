#include "retry_state.h"
#include "esp_attr.h"
#include "config.h"
#include <string.h>

RTC_DATA_ATTR static status_view_t s_shown;   // azzerata: outcome NONE, mai mostrato

const status_view_t *retry_state_shown(void) { return &s_shown; }
void                 retry_state_set_shown(const status_view_t *view) { s_shown = *view; }

RTC_DATA_ATTR static char s_digest[SB_DIGEST_ETAG_MAX];   // "" = nessuna immagine mostrata

const char *retry_state_digest_shown(void) { return s_digest; }
void retry_state_set_digest_shown(const char *etag)
{
    // etag troppo lungo: meglio "nessuna" di una copia troncata (ridisegno a ogni wake)
    strlcpy(s_digest, digest_etag_storable(etag) ? etag : "", sizeof(s_digest));
}
