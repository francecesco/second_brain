#include "power.h"
#include "board.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "power";

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
// power_boot_mode() apre percio' una finestra subito dopo il boot e
// campiona il tasto a intervalli; se rileva una pressione durante la
// finestra, riporta BOOT_DEV, altrimenti BOOT_NORMAL.
//
// Fix round 1: finestra allargata a 4.0s (era 2.5s) per essere piu'
// tollerante ai tempi di reazione umani, e aggiunta instrumentazione
// (livello raw del GPIO a inizio finestra e periodicamente durante il
// poll) per capire, da una cattura seriale, se il tasto viene letto
// correttamente oppure se il problema e' solo di timing.
#define POWER_BOOT_WINDOW_MS 4000
#define POWER_BOOT_POLL_MS   50
#define POWER_BOOT_LOG_EVERY_MS 250

void power_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BOARD_BTN_USER,
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

boot_mode_t power_boot_mode(void)
{
    int raw = gpio_get_level(BOARD_BTN_USER);
    ESP_LOGI(TAG, "boot-mode: BTN_USER raw level at start = %d (pressed=%d)",
             raw, power_button_pressed());
    ESP_LOGI(TAG, "boot-mode: premi ora il tasto utente per DEV (finestra 4s)");

    TickType_t start = xTaskGetTickCount();
    TickType_t last_log = start;
    while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(POWER_BOOT_WINDOW_MS)) {
        TickType_t now = xTaskGetTickCount();
        int elapsed_ms = (int)((now - start) * portTICK_PERIOD_MS);

        if (power_button_pressed()) {
            ESP_LOGI(TAG, "boot-mode: press detected at t=%dms -> DEV", elapsed_ms);
            return BOOT_DEV;
        }

        if ((now - last_log) >= pdMS_TO_TICKS(POWER_BOOT_LOG_EVERY_MS)) {
            ESP_LOGI(TAG, "boot-mode poll t=%dms raw=%d pressed=%d",
                     elapsed_ms, gpio_get_level(BOARD_BTN_USER), power_button_pressed());
            last_log = now;
        }

        vTaskDelay(pdMS_TO_TICKS(POWER_BOOT_POLL_MS));
    }
    ESP_LOGI(TAG, "boot-mode: nessuna pressione rilevata entro %dms -> NORMAL",
             (int)POWER_BOOT_WINDOW_MS);
    return BOOT_NORMAL;
}
