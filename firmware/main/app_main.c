#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "fw_version.h"
#include "display.h"
#include "power.h"
#include "storage.h"
#include "audio.h"
#include <string.h>

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());

    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", mode == BOOT_DEV ? "DEV" : "NORMAL");

    ESP_ERROR_CHECK(display_init());

    display_text("secondbrain", mode == BOOT_DEV ? "DEV MODE" : fw_version());

    ESP_ERROR_CHECK(storage_mount());
    const char *msg = "hello-sd";
    ESP_ERROR_CHECK(storage_write("bringup.txt", (const uint8_t *)msg, strlen(msg)));
    uint8_t rb[16] = {0};
    size_t n = 0;
    ESP_ERROR_CHECK(storage_read("bringup.txt", rb, sizeof(rb), &n));
    ESP_LOGI(TAG, "SD read back (%d): %.*s", (int)n, (int)n, rb);

    ESP_ERROR_CHECK(audio_init());
    ESP_LOGI(TAG, "recording 3s...");
    ESP_ERROR_CHECK(audio_record_wav("bringup.wav", 3));
    ESP_LOGI(TAG, "recording done");

    while (true) {
        ESP_LOGI(TAG, "alive");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
