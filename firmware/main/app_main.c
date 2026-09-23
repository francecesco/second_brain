#include <stdio.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "fw_version.h"
#include "display.h"
#include "power.h"
#include "storage.h"
#include "audio.h"
#include "sensors.h"
#include "wifi.h"
#include "ota.h"
#include "secrets.h"
#include "config.h"
#include "capture.h"
#include "queue.h"
#include "capture_name.h"
#include <string.h>

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());

    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", power_boot_mode_name(mode));

    ESP_ERROR_CHECK(display_init());

    display_text("secondbrain", mode == BOOT_DEV ? "DEV MODE" : fw_version());

    // Self-check superato (boot + display): se questa immagine e' appena
    // arrivata via OTA, confermala ora, prima di periferiche pesanti e
    // soprattutto prima del deep sleep (il wake passa dal bootloader, che
    // farebbe rollback di un'immagine non confermata).
    ota_mark_valid_if_pending();

    ESP_ERROR_CHECK(storage_mount());
    ESP_ERROR_CHECK(audio_init());
    if (mode == BOOT_CAPTURE) {
        ESP_ERROR_CHECK(queue_init());
        char part[QUEUE_PATH_MAX + 32], id[CAPTURE_NAME_MAX + 8];
        ESP_ERROR_CHECK(queue_new_part_path(part, sizeof(part), id, sizeof(id)));
        ESP_ERROR_CHECK(capture_start(part));
        display_text("REC", id);
        int released = 0;
        while (released < 2 && capture_is_running()) {   // rilascio su 2 campioni consecutivi
            vTaskDelay(pdMS_TO_TICKS(50));
            released = power_pwr_pressed() ? 0 : released + 1;
        }
        capture_result_t r;
        capture_stop(&r);
        if (r.duration_ms >= SB_CAPTURE_MIN_MS) queue_commit(part); else queue_discard(part);
        uint64_t bytes = 0; int n = queue_count(&bytes);
        ESP_LOGI(TAG, "hold-to-record: %lu ms; coda: %d file, %llu byte", (unsigned long)r.duration_ms, n, (unsigned long long)bytes);
    }

    ESP_ERROR_CHECK(sensors_init());
    float temp_c = 0, humidity = 0, bat_v = 0;
    int bat_pct = 0;
    if (sensors_read_climate(&temp_c, &humidity) == ESP_OK) {
        ESP_LOGI(TAG, "climate: temp %.1fC hum %.0f%%", temp_c, humidity);
    }
    if (sensors_read_battery(&bat_v, &bat_pct) == ESP_OK) {
        ESP_LOGI(TAG, "battery: %.2fV (%d%%)", bat_v, bat_pct);
    }
    struct tm now;
    if (sensors_read_time(&now) == ESP_OK) {
        ESP_LOGI(TAG, "rtc: %04d-%02d-%02d %02d:%02d:%02d",
                 now.tm_year + 1900, now.tm_mon + 1, now.tm_mday,
                 now.tm_hour, now.tm_min, now.tm_sec);
    }

    bool wifi_ok = false;
    if (wifi_connect(15000) == ESP_OK) {
        wifi_ok = true;
        char ip[16];
        wifi_get_ip(ip, sizeof(ip));
        ESP_LOGI(TAG, "wifi ok, ip=%s", ip);
        display_text("wifi ok", ip);
    } else {
        ESP_LOGW(TAG, "wifi failed");
    }

    if (mode != BOOT_DEV && wifi_ok) {
        ESP_LOGI(TAG, "checking OTA manifest...");
        esp_err_t r = ota_pull(OTA_MANIFEST_URL);
        ESP_LOGI(TAG, "ota_pull -> %s", esp_err_to_name(r));
    }

    if (mode != BOOT_DEV) {
        ESP_LOGI(TAG, "entering deep sleep; press PWR to wake");
        display_text("secondbrain", "sleeping");
        vTaskDelay(pdMS_TO_TICKS(500));
        power_deep_sleep();
        return; // mai raggiunto: power_deep_sleep() non ritorna.
    }

    // DEV boot: resta sveglia (nessun deep sleep) per lasciare la console
    // disponibile durante lo sviluppo/debug, e accetta firmware via
    // POST /ota (OTA push) se il Wi-Fi e' su.
    if (wifi_ok) {
        char ip[16];
        wifi_get_ip(ip, sizeof(ip));
        ESP_ERROR_CHECK(ota_dev_server_start());
        ESP_LOGI(TAG, "DEV OTA ready: curl --data-binary @build/secondbrain_fw.bin http://%s/ota", ip);
        display_text("DEV MODE", ip);
    } else {
        ESP_LOGW(TAG, "DEV mode senza Wi-Fi: OTA push non disponibile");
    }
    while (true) {
        ESP_LOGI(TAG, "alive (DEV mode, no sleep)");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
