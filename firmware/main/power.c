#include "power.h"
#include "board.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "power";

// Trigger dev-mode al boot: BOARD_BTN_PWR (GPIO18), non BOARD_BTN_USER.
//
// BOARD_BTN_USER e' collegato a GPIO0, che sulla ESP32-S3 e' anche il pin di
// strapping BOOT: se la ROM lo trova basso durante il reset/power-on, il
// chip entra in modalita' download seriale invece di avviare il nostro
// firmware. Per questo il tasto USER non puo' essere tenuto premuto durante
// il reset, e un rilevamento "dev mode" basato su di esso e' costretto a un
// pattern fragile (finestra di polling DOPO il boot, sincronizzata a mano
// con l'istante del reset) verificato su hardware reale come poco
// affidabile in pratica.
//
// BOARD_BTN_PWR (GPIO18) NON e' un pin di strapping: puo' essere tenuto
// premuto per tutta la sequenza power-up/reset senza alcun rischio di
// entrare in download mode. Possiamo quindi campionarlo una sola volta,
// subito dopo il boot, con un semplice debounce: se risulta premuto in
// tutti i campioni presi in una finestra breve (~300ms) -> dev mode.
// Verificato su hardware (log diagnostico round 2): la pressione di PWR
// porta BOARD_BTN_PWR stabilmente a livello basso.
//
// BOARD_BTN_USER resta disponibile per altri usi (capture, wake generico),
// tramite power_button_pressed().
#define POWER_PWR_DEBOUNCE_SAMPLES 6
#define POWER_PWR_DEBOUNCE_STEP_MS 50   // ~300ms totali (6 x 50ms)

void power_init(void)
{
    // BOARD_BTN_USER e BOARD_BTN_PWR configurati insieme, stessa polarita'
    // attivo-basso (pull-up interno).
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << BOARD_BTN_USER) | (1ULL << BOARD_BTN_PWR),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = BOARD_BTN_ACTIVE_LOW ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE,
        .pull_down_en = BOARD_BTN_ACTIVE_LOW ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io));
}

bool power_button_pressed(void)
{
    int lvl = gpio_get_level(BOARD_BTN_USER);
    return BOARD_BTN_ACTIVE_LOW ? (lvl == 0) : (lvl == 1);
}

static bool power_pwr_pressed(void)
{
    int lvl = gpio_get_level(BOARD_BTN_PWR);
    return BOARD_BTN_ACTIVE_LOW ? (lvl == 0) : (lvl == 1);
}

boot_mode_t power_boot_mode(void)
{
    int raw = gpio_get_level(BOARD_BTN_PWR);
    ESP_LOGI(TAG, "boot-mode: BTN_PWR raw level at start = %d (pressed=%d)",
             raw, power_pwr_pressed());

    bool held = true;
    for (int i = 0; i < POWER_PWR_DEBOUNCE_SAMPLES; i++) {
        if (!power_pwr_pressed()) {
            held = false;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(POWER_PWR_DEBOUNCE_STEP_MS));
    }

    boot_mode_t mode = held ? BOOT_DEV : BOOT_NORMAL;
    ESP_LOGI(TAG, "boot-mode: BTN_PWR %s su %d campioni (%dms) -> %s",
             held ? "tenuto premuto" : "non tenuto premuto",
             POWER_PWR_DEBOUNCE_SAMPLES,
             POWER_PWR_DEBOUNCE_SAMPLES * POWER_PWR_DEBOUNCE_STEP_MS,
             mode == BOOT_DEV ? "DEV" : "NORMAL");
    return mode;
}
