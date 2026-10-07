#pragma once
#include <stdbool.h>
// Codice HTTP della POST /captures -> azione sul file in coda (spec HTTPS+token §6). Pura.
typedef enum {
    SYNC_ACTION_DELETE,    // 200/201 accettata, 409 duplicato
    SYNC_ACTION_REJECT,    // altri 4xx: sposta in rejected/, prosegui
    SYNC_ACTION_STOP,      // 429, 5xx, redirect, errore rete (status <= 0): tieni, interrompi il sync
    SYNC_ACTION_STOP_AUTH, // 401/403: token assente, sbagliato o di un altro device: tieni, interrompi, segnala
} sync_action_t;
sync_action_t sync_decide(int http_status);
// 401 o 403: il server ha rifiutato il token (o il device non corrisponde al token).
bool http_status_is_auth_error(int http_status);
