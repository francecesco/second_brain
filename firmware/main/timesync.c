#include "timesync.h"
#include "timesync_policy.h"
#include "capture_name.h"
#include "sensors.h"
#include "config.h"

#include <time.h>
#include <sys/time.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "nvs.h"

static const char *TAG = "timesync";

void timesync_load_rtc(void)
{
    setenv("TZ", SB_TZ, 1); tzset();
    struct tm t;
    esp_err_t err = sensors_read_time(&t);
    if (err != ESP_OK) { ESP_LOGW(TAG, "RTC non leggibile: %s", esp_err_to_name(err)); return; }
    struct timeval tv = { .tv_sec = (time_t)timesync_tm_to_epoch_utc(&t), .tv_usec = 0 };
    settimeofday(&tv, NULL);
    ESP_LOGI(TAG, "ora da RTC: %04d-%02d-%02dT%02d:%02d:%02dZ valido=%d",
             t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, timesync_rtc_valid());
}

bool timesync_rtc_valid(void)
{
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc);
    return capture_name_rtc_valid(&utc);
}

static int64_t nvs_get_lastntp(void)
{
    nvs_handle_t h; int64_t v = 0;
    if (nvs_open(SB_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return 0;
    nvs_get_i64(h, SB_NVS_KEY_LASTNTP, &v);
    nvs_close(h);
    return v;
}

static void nvs_set_lastntp(int64_t v)
{
    nvs_handle_t h;
    if (nvs_open(SB_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_i64(h, SB_NVS_KEY_LASTNTP, v); nvs_commit(h); nvs_close(h);
}

esp_err_t timesync_run(void)
{
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc);
    if (!timesync_needed(utc.tm_year + 1900, nvs_get_lastntp(), (int64_t)now)) {
        ESP_LOGI(TAG, "RTC valido e NTP recente: niente da fare");
        return ESP_OK;
    }
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG(SB_NTP_SERVER);
    esp_err_t err = esp_netif_sntp_init(&cfg);
    if (err != ESP_OK) { ESP_LOGE(TAG, "sntp init: %s", esp_err_to_name(err)); return err; }
    err = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(SB_NTP_WAIT_MS));
    esp_netif_sntp_deinit();
    if (err != ESP_OK) { ESP_LOGW(TAG, "SNTP fallito (%s): resto con l'ora dell'RTC", esp_err_to_name(err)); return err; }

    now = time(NULL); gmtime_r(&now, &utc);
    if (sensors_set_time(&utc) == ESP_OK) {
        nvs_set_lastntp((int64_t)now);
        ESP_LOGI(TAG, "RTC aggiornato da NTP: %04d-%02d-%02dT%02d:%02d:%02dZ",
                 utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec);
    }
    return ESP_OK;
}
