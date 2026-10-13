#pragma once
#include "sync_retry_policy.h"
// Ultima schermata di stato mostrata, conservata attraverso il deep sleep (memoria RTC, spec
// wake periodico §4). Si azzera a un power-on o dopo un riavvio OTA: costa al piu' un
// refresh in piu'.
const status_view_t *retry_state_shown(void);
void                 retry_state_set_shown(const status_view_t *view);

// Etag dell'immagine Oggi mostrata, conservato come lo stato sopra.
const char *retry_state_digest_shown(void);          // "" se nessuna
void        retry_state_set_digest_shown(const char *etag);   // NULL = nessuna
