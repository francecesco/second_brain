#include "status_http.h"
#include "ota.h"
#include "queue.h"
#include "storage.h"
#include "sensors.h"
#include "timesync.h"
#include "fw_version.h"
#include "diag.h"

#include <stdlib.h>
#include <time.h>
#include "esp_log.h"
#include "cJSON.h"

static const char *TAG = "status";

static esp_err_t status_get(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "version", fw_version());
    float v = 0; int pct = -1;
    if (sensors_init() == ESP_OK && sensors_read_battery(&v, &pct) == ESP_OK) {
        cJSON_AddNumberToObject(root, "battery_pct", pct);
        cJSON_AddNumberToObject(root, "battery_v", (double)((int)(v * 100)) / 100.0);
    }
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc);
    char ts[24];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &utc);
    cJSON_AddStringToObject(root, "time", ts);
    cJSON_AddBoolToObject(root, "rtc_valid", timesync_rtc_valid());

    cJSON *arr = cJSON_AddArrayToObject(root, "queue");
    uint64_t bytes = 0;
    if (storage_mount() == ESP_OK && queue_init() == ESP_OK) {
        char (*names)[QUEUE_PATH_MAX] = calloc(64, QUEUE_PATH_MAX);
        if (names) {
            int n = queue_list(names, 64);
            for (int i = 0; i < n; i++) cJSON_AddItemToArray(arr, cJSON_CreateString(names[i]));
            free(names);
        }
        queue_count(&bytes);
        cJSON_AddBoolToObject(root, "sd", true);
    } else {
        cJSON_AddBoolToObject(root, "sd", false);
    }
    cJSON_AddNumberToObject(root, "queue_bytes", (double)bytes);

    const diag_t *d = diag_get();
    cJSON *lc = cJSON_AddObjectToObject(root, "last_cycle");
    cJSON_AddNumberToObject(lc, "seq", d->seq);
    cJSON_AddStringToObject(lc, "mode", d->mode);
    cJSON_AddNumberToObject(lc, "t_capture_start_ms", d->t_capture_start);
    cJSON_AddNumberToObject(lc, "t_rec_shown_ms", d->t_rec_shown);
    cJSON_AddNumberToObject(lc, "t_wifi_connected_ms", d->t_wifi_connected);
    cJSON_AddNumberToObject(lc, "t_cycle_end_ms", d->t_cycle_end);
    cJSON_AddNumberToObject(lc, "capture_ms", d->capture_ms);
    cJSON_AddNumberToObject(lc, "peak", d->peak);
    cJSON_AddNumberToObject(lc, "upload_bytes", d->upload_bytes);
    cJSON_AddNumberToObject(lc, "upload_ms", d->upload_ms);
    if (d->upload_ms > 0) cJSON_AddNumberToObject(lc, "upload_kbps", (double)d->upload_bytes / d->upload_ms);

    char *txt = cJSON_PrintUnformatted(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, txt ? txt : "{}");
    free(txt);
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t status_http_register(void)
{
    const httpd_uri_t uri = { .uri = "/status", .method = HTTP_GET, .handler = status_get };
    esp_err_t err = ota_dev_server_register(&uri);
    if (err == ESP_OK) ESP_LOGI(TAG, "GET /status registrato");
    return err;
}
