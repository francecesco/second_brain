#include "digest.h"

#include <stdio.h>
#include <stdbool.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "esp_log.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "config.h"
#include "secrets.h"
#include "storage.h"
#include "http_client.h"
#include "sync_policy.h"
#include "sync_retry_policy.h"

static const char *TAG = "digest";

#define DIGEST_PATH      STORAGE_MOUNT "/" SB_DIGEST_FILE
#define DIGEST_ETAG_PATH STORAGE_MOUNT "/" SB_DIGEST_ETAG_FILE
#define DIGEST_TMP_SUFFIX ".tmp"
#define DIGEST_PATH_MAX  64

const char *digest_fetch_name(digest_fetch_t r)
{
    switch (r) {
    case DIGEST_NEW:  return "new";
    case DIGEST_SAME: return "same";
    case DIGEST_NONE: return "none";
    default:          return "failed";
    }
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

// esp_http_client_get_header legge solo gli header della richiesta: l'ETag della risposta si
// prende dall'evento HTTP_EVENT_ON_HEADER, che arriva durante fetch_headers.
typedef struct {
    char  *etag;
    size_t len;
    bool   too_long;
} etag_capture_t;

static esp_err_t on_http_event(esp_http_client_event_t *evt)
{
    if (evt->event_id != HTTP_EVENT_ON_HEADER || !evt->user_data) return ESP_OK;
    if (!evt->header_key || !evt->header_value || strcasecmp(evt->header_key, "ETag") != 0) return ESP_OK;
    etag_capture_t *c = evt->user_data;
    // Troncato non combacerebbe piu' col server: meglio nessun etag.
    c->too_long = strlen(evt->header_value) >= c->len;
    strlcpy(c->etag, c->too_long ? "" : evt->header_value, c->len);
    return ESP_OK;
}

static digest_fetch_t fetch_close(esp_http_client_handle_t client, digest_fetch_t r)
{
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return r;
}

// Modello: http_get_to_buffer di ota.c (open con un retry, fetch_headers, status, read in loop
// che chiama sempre esp_http_client_read prima di guardare se il corpo e' finito).
digest_fetch_t digest_fetch(const char *etag_in, uint8_t *buf, char *etag_out, size_t etag_len)
{
    etag_out[0] = '\0';
    etag_capture_t cap = { .etag = etag_out, .len = etag_len };

    esp_http_client_config_t config = http_client_config(SERVER_BASE_URL "/digest/today", HTTP_METHOD_GET,
                                                         SB_DIGEST_TIMEOUT_MS);
    config.event_handler = on_http_event;
    config.user_data = &cap;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return DIGEST_FAILED;
    http_client_set_auth(client);
    if (etag_in && etag_in[0]) esp_http_client_set_header(client, "If-None-Match", etag_in);

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        // Come ota.c: la prima connessione dopo l'associazione a volte abortisce.
        ESP_LOGW(TAG, "http open failed: %s, riprovo tra 1 s", esp_err_to_name(err));
        vTaskDelay(pdMS_TO_TICKS(1000));
        err = esp_http_client_open(client, 0);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "http open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return DIGEST_FAILED;
    }

    int64_t content_length = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if (status == 304) return fetch_close(client, DIGEST_SAME);
    if (status == 204) return fetch_close(client, DIGEST_NONE);
    if (status != 200) {
        // Il token rifiutato lo segnala gia' l'OTA pull ("token ko"): qui solo il log.
        if (http_status_is_auth_error(status)) ESP_LOGE(TAG, "token rifiutato (HTTP %d)", status);
        else ESP_LOGE(TAG, "GET status %d", status);
        return fetch_close(client, DIGEST_FAILED);
    }
    if (cap.too_long || !etag_out[0]) {
        ESP_LOGE(TAG, "ETag %s nella risposta", cap.too_long ? "troppo lungo" : "assente");
        return fetch_close(client, DIGEST_FAILED);
    }
    if (content_length > 0 && content_length != SB_DIGEST_BYTES) {
        ESP_LOGE(TAG, "dimensione %lld invece di %d", (long long)content_length, SB_DIGEST_BYTES);
        return fetch_close(client, DIGEST_FAILED);
    }

    size_t total = 0;
    while (total < SB_DIGEST_BYTES) {
        int r = esp_http_client_read(client, (char *)buf + total, SB_DIGEST_BYTES - total);
        if (r < 0) {
            ESP_LOGE(TAG, "errore di lettura a byte %u", (unsigned)total);
            return fetch_close(client, DIGEST_FAILED);
        }
        if (r == 0) break;
        total += (size_t)r;
        if (esp_http_client_is_complete_data_received(client)) break;
    }
    if (total != SB_DIGEST_BYTES) {
        ESP_LOGE(TAG, "immagine incompleta: %u/%d byte", (unsigned)total, SB_DIGEST_BYTES);
        return fetch_close(client, DIGEST_FAILED);
    }
    if (content_length <= 0) {
        // Senza Content-Length (chunked) un corpo piu' lungo si vede solo leggendo oltre.
        char extra;
        if (esp_http_client_read(client, &extra, 1) > 0) {
            ESP_LOGE(TAG, "immagine piu' lunga di %d byte", SB_DIGEST_BYTES);
            return fetch_close(client, DIGEST_FAILED);
        }
    }
    return fetch_close(client, DIGEST_NEW);
}

// ---------------------------------------------------------------------------
// SD
// ---------------------------------------------------------------------------

// Scrive su <path>.tmp e lo rinomina in <path>. Su FAT rename() non sovrascrive un file
// esistente: il vecchio va cancellato prima, e se il device si spegne proprio tra unlink e
// rename resta solo il .tmp (al ciclo dopo digest_load da' NOT_FOUND e si riscarica). Il file
// finale invece non e' mai a meta'.
static esp_err_t write_atomic(const char *path, const void *data, size_t len)
{
    char tmp[DIGEST_PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s" DIGEST_TMP_SUFFIX, path);
    FILE *f = fopen(tmp, "wb");
    if (!f) {
        ESP_LOGE(TAG, "fopen %s fallita", tmp);
        return ESP_FAIL;
    }
    bool ok = fwrite(data, 1, len, f) == len && fflush(f) == 0 && fsync(fileno(f)) == 0;
    ok = (fclose(f) == 0) && ok;
    if (!ok) {
        ESP_LOGE(TAG, "scrittura di %s fallita", tmp);
        unlink(tmp);
        return ESP_FAIL;
    }
    unlink(path);
    if (rename(tmp, path) != 0) {
        ESP_LOGE(TAG, "rename %s fallita", tmp);
        unlink(tmp);
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t digest_save(const uint8_t *buf, const char *etag)
{
    if (!digest_etag_storable(etag) || !etag[0]) return ESP_ERR_INVALID_ARG;
    // Prima via l'etag: un'immagine nuova con l'etag vecchio farebbe credere al server che il
    // device abbia gia' quella giusta. Senza etag digest_load da' NOT_FOUND: si riscarica.
    unlink(DIGEST_ETAG_PATH);
    esp_err_t err = write_atomic(DIGEST_PATH, buf, SB_DIGEST_BYTES);
    if (err != ESP_OK) return err;
    return write_atomic(DIGEST_ETAG_PATH, etag, strlen(etag));
}

esp_err_t digest_load(uint8_t *buf, char *etag, size_t etag_len)
{
    etag[0] = '\0';
    FILE *f = fopen(DIGEST_PATH, "rb");
    if (!f) return ESP_ERR_NOT_FOUND;
    long size = -1;
    if (fseek(f, 0, SEEK_END) == 0) size = ftell(f);
    if (!digest_size_valid(size)) {
        fclose(f);
        ESP_LOGW(TAG, "%s di %ld byte invece di %d: cancellato", DIGEST_PATH, size, SB_DIGEST_BYTES);
        digest_clear();
        return ESP_ERR_INVALID_SIZE;
    }
    rewind(f);
    size_t got = fread(buf, 1, SB_DIGEST_BYTES, f);
    fclose(f);
    if (got != SB_DIGEST_BYTES) return ESP_FAIL;

    FILE *e = fopen(DIGEST_ETAG_PATH, "rb");
    if (!e) return ESP_ERR_NOT_FOUND;
    size_t n = fread(etag, 1, etag_len - 1, e);
    fclose(e);
    etag[n] = '\0';
    if (n == 0) return ESP_ERR_NOT_FOUND;
    return ESP_OK;
}

void digest_clear(void)
{
    unlink(DIGEST_ETAG_PATH);
    unlink(DIGEST_PATH);
}
