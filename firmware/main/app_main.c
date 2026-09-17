#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "fw_version.h"
#include "display.h"

static const char *TAG = "app";

// Fix round 1 (Task 3, Finding A): buffer di test "prova di pixel", usato solo
// per verificare che il pannello risponda a un refresh completo prima di
// fidarsi del rendering del testo/font. Statico (non sullo stack: il task
// stack di app_main non garantisce 5000 byte liberi).
static uint8_t s_test_pattern[(DISPLAY_W * DISPLAY_H) / 8];

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());

    ESP_ERROR_CHECK(display_init());

    // Metà superiore nera, metà inferiore bianca: se questo non si vede sul
    // pannello fisico, il problema è nel percorso SPI/init/refresh, non nel
    // font o nel testo. bit=1 -> nero (vedi display.c: display_blit_1bit).
    ESP_LOGI(TAG, "test pattern: meta' superiore NERA / meta' inferiore BIANCA");
    memset(s_test_pattern, 0xFF, sizeof(s_test_pattern) / 2);
    memset(s_test_pattern + sizeof(s_test_pattern) / 2, 0x00, sizeof(s_test_pattern) / 2);
    display_blit_1bit(s_test_pattern, DISPLAY_W, DISPLAY_H);
    vTaskDelay(pdMS_TO_TICKS(4000));

    display_text("secondbrain", fw_version());

    while (true) {
        ESP_LOGI(TAG, "alive");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
