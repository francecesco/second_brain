#pragma once
#include "sync_retry_policy.h"
// Stato del timer di sync che sopravvive al deep sleep (memoria RTC, spec timer §5):
// tentativi consecutivi da timer e ultimo esito mostrato sul display. Si azzera a un
// power-on o dopo un riavvio OTA: costa al piu' un refresh in piu'.
unsigned         retry_state_attempt(void);
void             retry_state_bump(void);      // un altro wake da timer senza svuotare la coda
void             retry_state_reset(void);     // wake da tasto o coda vuota
status_outcome_t retry_state_shown(void);
void             retry_state_set_shown(status_outcome_t outcome);
