#include "power.h"
#include "board.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// Finestra (e passo) di polling per il rilevamento del boot mode.
//
// BOARD_BTN_USER e' collegato a GPIO0, che sulla ESP32-S3 e' anche il pin di
// strapping BOOT: se viene letto basso dalla ROM durante il reset/power-on,
// il chip entra in modalita' download seriale invece di avviare il nostro
// firmware. Per questo NON possiamo implementare "tasto tenuto premuto al
// reset -> dev mode" leggendo il pin a freddo: bloccherebbe l'avvio.
//
// Il repo di riferimento Waveshare (button_bsp nei suoi esempi, es.
// 02_Example/ESP-IDF/V2/12_RTC_Sleep_Test/components/button_bsp/button_bsp.c)
// conferma questo pattern: BOOT_BUTTON_PIN (= GPIO0) viene configurato come
// input con pull-up e poi letto a runtime tramite un task/timer periodico,
// mai campionato "a bordo" del reset. Adottiamo lo stesso principio: lo
// strapping viene consumato dalla ROM prima che app_main() giri, quindi
// leggere il GPIO *dopo* l'avvio dell'app e' sicuro.
//
// power_boot_mode() apre percio' una breve finestra subito dopo il boot e
// campiona il tasto a intervalli; se rileva una pressione durante la
// finestra, riporta BOOT_DEV, altrimenti BOOT_NORMAL.
#define POWER_BOOT_WINDOW_MS 2500
#define POWER_BOOT_POLL_MS   50

void power_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BOARD_BTN_USER,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = BOARD_BTN_ACTIVE_LOW ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE,
        .pull_down_en = BOARD_BTN_ACTIVE_LOW ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
}

bool power_button_pressed(void)
{
    int lvl = gpio_get_level(BOARD_BTN_USER);
    return BOARD_BTN_ACTIVE_LOW ? (lvl == 0) : (lvl == 1);
}

boot_mode_t power_boot_mode(void)
{
    TickType_t start = xTaskGetTickCount();
    while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(POWER_BOOT_WINDOW_MS)) {
        if (power_button_pressed()) {
            return BOOT_DEV;
        }
        vTaskDelay(pdMS_TO_TICKS(POWER_BOOT_POLL_MS));
    }
    return BOOT_NORMAL;
}
