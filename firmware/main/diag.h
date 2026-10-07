#pragma once
#include <stdint.h>
// Diagnostica dell'ultimo ciclo, conservata in RTC slow memory attraverso il deep sleep
// e letta da GET /status. Tempi in ms dall'avvio dell'app (il bootloader aggiunge ~0.4 s).
typedef struct {
    uint32_t seq;               // contatore cicli
    char     mode[12];          // SYNC_ONLY | CAPTURE | DEV
    uint32_t t_capture_start;   // registrazione avviata
    uint32_t t_rec_shown;       // "* REC" visibile (fine refresh)
    uint32_t t_wifi_connected;  // IP ottenuto
    uint32_t t_cycle_end;       // deep sleep
    uint32_t capture_ms;        // durata registrazione
    uint32_t upload_bytes;      // byte caricati nel ciclo
    uint32_t upload_ms;         // tempo di upload totale
    int32_t  peak;              // picco audio della cattura (0..32767)
    int32_t  last_http;         // status HTTP dell'ultimo upload (0 = nessuno/errore rete)
    uint32_t free_heap_min;     // heap libero minimo dal boot (byte, 0 = mai letto): include l'handshake TLS
} diag_t;

diag_t *diag_begin(const char *mode);   // azzera e apre il record del ciclo corrente
diag_t *diag_get(void);                 // record corrente (o dell'ultimo ciclo, dopo il wake)
uint32_t diag_now_ms(void);             // ms dall'avvio dell'app
void     diag_note_heap(void);          // aggiorna free_heap_min con l'heap libero attuale
