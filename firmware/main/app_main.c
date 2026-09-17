#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "fw_version.h"
#include "display.h"
#include "power.h"

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());

    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", mode == BOOT_DEV ? "DEV" : "NORMAL");

    ESP_ERROR_CHECK(display_init());

    display_text("secondbrain", mode == BOOT_DEV ? "DEV MODE" : fw_version());

    while (true) {
        ESP_LOGI(TAG, "alive");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
