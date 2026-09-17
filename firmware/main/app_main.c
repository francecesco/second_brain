#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "fw_version.h"
#include "display.h"
#include "power.h"
#include "board.h"

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());

    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", mode == BOOT_DEV ? "DEV" : "NORMAL");

    ESP_ERROR_CHECK(display_init());

    display_text("secondbrain", mode == BOOT_DEV ? "DEV MODE" : fw_version());

    // Diagnostica temporanea bring-up: log continuo dello stato di
    // ENTRAMBI i tasti fisici (BTN_USER/GPIO0 e BTN_PWR), per mappare
    // quale GPIO risponde davvero alla pressione del tasto BOOT sulla
    // scheda reale, senza dipendere dal timing della finestra di boot mode.
    while (true) {
        ESP_LOGI(TAG, "alive | BTN_USER(GPIO%d) raw=%d | BTN_PWR(GPIO%d) raw=%d",
                 BOARD_BTN_USER, gpio_get_level(BOARD_BTN_USER),
                 BOARD_BTN_PWR,  gpio_get_level(BOARD_BTN_PWR));
        vTaskDelay(pdMS_TO_TICKS(400));
    }
}
