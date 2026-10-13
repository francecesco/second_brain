#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

// Schermata "Oggi" (spec Home Assistant): immagine SB_DIGEST_W x SB_DIGEST_H gia' impacchettata
// dal backend (SB_DIGEST_BYTES, formato del framebuffer), scaricata con ETag e tenuta sulla SD
// per i cicli senza rete.

typedef enum {
    DIGEST_NEW,     // 200: buf ed etag_out pieni
    DIGEST_SAME,    // 304: l'immagine di etag_in e' ancora quella giusta
    DIGEST_NONE,    // 204: Home Assistant non configurato, niente immagine
    DIGEST_FAILED,  // rete, 4xx/5xx, dimensione sbagliata, ETag assente: buf non utilizzabile
} digest_fetch_t;

// GET SERVER_BASE_URL/digest/today con token e If-None-Match (se etag_in non e' vuoto).
// buf deve avere SB_DIGEST_BYTES byte.
digest_fetch_t digest_fetch(const char *etag_in, uint8_t *buf, char *etag_out, size_t etag_len);
const char    *digest_fetch_name(digest_fetch_t r);   // "new" | "same" | "none" | "failed"

// SD: immagine ed etag scritti su file temporanei e rinominati (mai un file a meta').
esp_err_t digest_save(const uint8_t *buf, const char *etag);
// ESP_ERR_NOT_FOUND se manca uno dei due file, ESP_ERR_INVALID_SIZE se l'immagine non ha la
// dimensione giusta (in quel caso i file vengono cancellati).
esp_err_t digest_load(uint8_t *buf, char *etag, size_t etag_len);
void      digest_clear(void);
