#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain firmware bring-up: hello");
    while (true) {
        ESP_LOGI(TAG, "alive");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
