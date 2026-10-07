#include "sync.h"
#include "sync_policy.h"
#include "queue.h"
#include "wav.h"
#include "config.h"
#include "fw_version.h"
#include "battery.h"
#include "wifi.h"
#include "secrets.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_http_client.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "diag.h"

static const char *TAG = "sync";
#define SYNC_CHUNK     16384
#define SYNC_MAX_FILES 64

// Ritorna il codice HTTP (>0) oppure <=0 su errore di rete/timeout.
static int upload_one(const char *name, const char *device_id, const char *bat_pct, const char *bat_v, const char *src)
{
    char abs[QUEUE_PATH_MAX + 32];
    queue_abs_path(name, abs, sizeof(abs));
    struct stat st;
    if (stat(abs, &st) != 0) return 400; // sparito o illeggibile: trattalo come non valido
    FILE *f = fopen(abs, "rb");
    if (!f) return 400;

    char id[QUEUE_PATH_MAX];
    strlcpy(id, name, sizeof(id));
    char *dot = strstr(id, ".wav"); if (dot) *dot = '\0';

    char url[192];
    snprintf(url, sizeof(url), "%s/captures", SERVER_BASE_URL);
    esp_http_client_config_t cfg = { .url = url, .method = HTTP_METHOD_POST, .timeout_ms = SB_HTTP_IDLE_TIMEOUT_MS };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) { fclose(f); return 0; }

    esp_http_client_set_header(c, "Content-Type", "audio/wav");
    esp_http_client_set_header(c, "X-Capture-Id", id);
    if (strncmp(id, "cap_unsynced_", 13) != 0 && strlen(id) >= 19) {
        // cap_YYYYMMDD_HHMMSS[...] -> YYYY-MM-DDTHH:MM:SSZ
        char ts[24];
        snprintf(ts, sizeof(ts), "%.4s-%.2s-%.2sT%.2s:%.2s:%.2sZ", id + 4, id + 8, id + 10, id + 13, id + 15, id + 17);
        esp_http_client_set_header(c, "X-Capture-Ts", ts);
    }
    esp_http_client_set_header(c, "X-Device-Id", device_id);
    esp_http_client_set_header(c, "X-Firmware-Version", fw_version());
    if (bat_pct[0]) esp_http_client_set_header(c, "X-Battery-Pct", bat_pct);
    if (bat_v[0])   esp_http_client_set_header(c, "X-Battery-Voltage", bat_v);
    if (src[0])     esp_http_client_set_header(c, "X-Power-Source", src);

    int status = 0;
    uint8_t *buf = NULL;
    esp_err_t err = esp_http_client_open(c, (int)st.st_size);
    if (err != ESP_OK) { ESP_LOGE(TAG, "open %s: %s", url, esp_err_to_name(err)); goto out; }

    buf = malloc(SYNC_CHUNK);
    if (!buf) goto out;
    size_t n; bool write_ok = true;
    while ((n = fread(buf, 1, SYNC_CHUNK, f)) > 0) {
        if (esp_http_client_write(c, (const char *)buf, (int)n) != (int)n) { write_ok = false; break; }
    }
    if (!write_ok) { ESP_LOGE(TAG, "write interrotta su %s", name); goto out; }
    if (esp_http_client_fetch_headers(c) < 0) { ESP_LOGE(TAG, "fetch_headers fallita su %s", name); goto out; }
    status = esp_http_client_get_status_code(c);
    // consuma il corpo (piccolo JSON) per chiudere pulito
    while (esp_http_client_read(c, (char *)buf, SYNC_CHUNK) > 0) {}
out:
    free(buf);
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    fclose(f);
    ESP_LOGI(TAG, "upload %s (%ld byte) -> HTTP %d", name, (long)st.st_size, status);
    return status;
}

esp_err_t sync_run(uint32_t window_ms, sync_result_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    char (*names)[QUEUE_PATH_MAX] = calloc(SYNC_MAX_FILES, QUEUE_PATH_MAX);
    if (!names) return ESP_ERR_NO_MEM;
    int n = queue_list(names, SYNC_MAX_FILES);
    ESP_LOGI(TAG, "coda: %d file da inviare", n);

    uint8_t mac[6]; char device_id[13] = "unknown";
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK)
        snprintf(device_id, sizeof(device_id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    // Stato dell'alimentazione letto a inizio ciclo (app_main): percentuale solo a batteria.
    char bat_pct[8] = "", bat_v[8] = "", src[12] = "";
    const power_state_t *ps = battery_last();
    if (ps->valid) {
        snprintf(bat_v, sizeof(bat_v), "%.2f", ps->mv / 1000.0);
        snprintf(src, sizeof(src), "%s", ps->source == POWER_SRC_USB ? "usb" : "battery");
        if (ps->source == POWER_SRC_BATTERY) snprintf(bat_pct, sizeof(bat_pct), "%d", ps->pct);
    }

    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < n; i++) {
        if ((esp_timer_get_time() - t0) / 1000 > (int64_t)window_ms) { ESP_LOGW(TAG, "finestra di sync esaurita"); break; }
        int64_t tu = esp_timer_get_time();
        int status = upload_one(names[i], device_id, bat_pct, bat_v, src);
        if (status > 0) {
            char abs[QUEUE_PATH_MAX + 32]; struct stat st;
            queue_abs_path(names[i], abs, sizeof(abs));
            if (stat(abs, &st) == 0) diag_get()->upload_bytes += (uint32_t)st.st_size;
            diag_get()->upload_ms += (uint32_t)((esp_timer_get_time() - tu) / 1000);
        }
        if (status <= 0) { // errore di rete (es. abort subito dopo l'associazione): un retry
            vTaskDelay(pdMS_TO_TICKS(1000));
            status = upload_one(names[i], device_id, bat_pct, bat_v, src);
        }
        switch (sync_decide(status)) {
        case SYNC_ACTION_DELETE: queue_delete(names[i]); out->sent++; break;
        case SYNC_ACTION_REJECT: queue_reject(names[i]); out->rejected++; ESP_LOGW(TAG, "%s rifiutato (HTTP %d)", names[i], status); break;
        case SYNC_ACTION_STOP:   out->server_error = true; ESP_LOGW(TAG, "sync interrotto (HTTP %d)", status); i = n; break;
        case SYNC_ACTION_STOP_AUTH:
            out->auth_error = true;
            ESP_LOGE(TAG, "token rifiutato dal server (HTTP %d): coda conservata, controlla DEVICE_TOKEN", status);
            i = n; break;
        }
    }
    free(names);
    out->remaining = queue_count(NULL);
    ESP_LOGI(TAG, "sync: inviate=%d rifiutate=%d restano=%d server_error=%d auth_error=%d (%lu byte in %lu ms)",
             out->sent, out->rejected, out->remaining, out->server_error, out->auth_error,
             (unsigned long)diag_get()->upload_bytes, (unsigned long)diag_get()->upload_ms);
    return (out->server_error || out->auth_error) ? ESP_FAIL : ESP_OK;
}
