#pragma once
// Codice HTTP della POST /captures -> azione sul file in coda (spec §6.2). Pura.
typedef enum {
    SYNC_ACTION_DELETE, // 200/201 accettata, 409 duplicato
    SYNC_ACTION_REJECT, // altri 4xx: sposta in rejected/, prosegui
    SYNC_ACTION_STOP,   // 5xx, redirect, errore rete (status <= 0): tieni, interrompi il sync
} sync_action_t;
sync_action_t sync_decide(int http_status);
