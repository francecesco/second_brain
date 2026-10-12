#include "power.h"
#include "board.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "config.h"

static const char *TAG = "power";

// Tasti e modalita' di boot (spec tasti §3). PWR (GPIO18, tasto sotto) registra, USER
// (GPIO0, tasto sopra) sincronizza: entrambi svegliano dal deep sleep (EXT1). GPIO0 e' un
// pin di strapping, ma lo strapping si legge solo all'accensione: il wake dal deep sleep
// con USER e' verificato sull'hardware il 2026-10-11 (nessuna modalita' download).

void power_init(void)
{
    // PRIMA di tutto: mantieni l'alimentazione da batteria (vedi BOARD_BAT_LATCH).
    // Dopo un wake da deep sleep il pin e' ancora in hold dal ciclo precedente.
    gpio_hold_dis(BOARD_BAT_LATCH);
    gpio_config_t latch = {
        .pin_bit_mask = 1ULL << BOARD_BAT_LATCH,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_set_level(BOARD_BAT_LATCH, 1);
    gpio_config(&latch);
    gpio_set_level(BOARD_BAT_LATCH, 1);

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

bool power_user_pressed(void)
{
    int lvl = gpio_get_level(BOARD_BTN_USER);
    return BOARD_BTN_ACTIVE_LOW ? (lvl == 0) : (lvl == 1);
}

bool power_pwr_pressed(void)
{
    int lvl = gpio_get_level(BOARD_BTN_PWR);
    return BOARD_BTN_ACTIVE_LOW ? (lvl == 0) : (lvl == 1);
}

boot_mode_t power_boot_mode(void)
{
    esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
    if (cause == ESP_SLEEP_WAKEUP_TIMER) {
        ESP_LOGI(TAG, "boot-mode: wake dal timer -> TIMER");
        return BOOT_TIMER_SYNC;
    }
    if (cause == ESP_SLEEP_WAKEUP_EXT1) {
        bool pwr = esp_sleep_get_ext1_wakeup_status() & (1ULL << BOARD_BTN_PWR);
        ESP_LOGI(TAG, "boot-mode: wake da %s -> %s", pwr ? "PWR" : "USER", pwr ? "CAPTURE" : "SYNC_ONLY");
        return pwr ? BOOT_CAPTURE : BOOT_SYNC_ONLY;
    }
    // Accensione o reset: con PWR giu' e' l'accensione a batteria col tasto della
    // registrazione; altrimenti USB inserita, reset, riavvio dopo OTA o DEV.
    if (power_pwr_pressed()) {
        ESP_LOGI(TAG, "boot-mode: accensione con PWR premuto -> CAPTURE");
        return BOOT_CAPTURE;
    }
    ESP_LOGI(TAG, "boot-mode: accensione senza tasti -> SYNC_ONLY");
    return BOOT_SYNC_ONLY;
}

const char *power_boot_mode_name(boot_mode_t m)
{
    switch (m) {
    case BOOT_DEV:     return "DEV";
    case BOOT_TIMER_SYNC: return "TIMER";
    case BOOT_CAPTURE: return "CAPTURE";
    default:           return "SYNC_ONLY";
    }
}

// --- scadenza di ciclo ------------------------------------------------------
static esp_timer_handle_t s_deadline = NULL;

static void deadline_cb(void *arg)
{
    (void)arg;
    ESP_LOGE(TAG, "scadenza di ciclo raggiunta: deep sleep forzato");
    power_deep_sleep(0); // senza timer: un ciclo bloccato non va ripetuto da soli
}

void power_cycle_deadline_start(uint32_t ms)
{
    if (s_deadline == NULL) {
        const esp_timer_create_args_t args = { .callback = deadline_cb, .name = "cycle_deadline" };
        if (esp_timer_create(&args, &s_deadline) != ESP_OK) {
            ESP_LOGE(TAG, "esp_timer_create scadenza fallita");
            return;
        }
    }
    esp_timer_stop(s_deadline); // ok anche se non attivo
    esp_timer_start_once(s_deadline, (uint64_t)ms * 1000ULL);
    ESP_LOGI(TAG, "scadenza di ciclo: %lu ms", (unsigned long)ms);
}

void power_cycle_deadline_cancel(void)
{
    if (s_deadline) esp_timer_stop(s_deadline);
}

// Wake da deep sleep su PWR (GPIO18) e USER (GPIO0), entrambi RTC GPIO sulla ESP32-S3.
// Il wake dal deep sleep non rilegge lo strapping: GPIO0 basso qui non porta in download
// mode (verificato il 2026-10-11). Lo farebbe solo premuto durante un'accensione da zero.
void power_deep_sleep(uint32_t timer_ms)
{
    // In deep sleep il dominio digitale (e con esso la pull-up impostata da
    // gpio_config in power_init) e' spento: per mantenere il pin in idle alto
    // durante il sonno serve una pull-up nel dominio RTC.
    rtc_gpio_pullup_en(BOARD_BTN_PWR);
    rtc_gpio_pulldown_dis(BOARD_BTN_PWR);
    rtc_gpio_pullup_en(BOARD_BTN_USER);
    rtc_gpio_pulldown_dis(BOARD_BTN_USER);

    // Tasti attivo-basso -> wake quando uno dei due scende (EXT1, ANY_LOW).
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup((1ULL << BOARD_BTN_PWR) | (1ULL << BOARD_BTN_USER),
                                                  ESP_EXT1_WAKEUP_ANY_LOW));
    if (timer_ms > 0) {
        esp_sleep_enable_timer_wakeup((uint64_t)timer_ms * 1000ULL);
        ESP_LOGI(TAG, "wake dal timer tra %lu s", (unsigned long)(timer_ms / 1000));
    }

    // Il mantenimento batteria deve restare alto anche durante il sonno, altrimenti a
    // batteria il deep sleep diventerebbe uno spegnimento fisico.
    gpio_set_level(BOARD_BAT_LATCH, 1);
    gpio_hold_en(BOARD_BAT_LATCH);
    gpio_deep_sleep_hold_en();

    ESP_LOGI(TAG, "entering deep sleep; PWR registra, USER sincronizza");
    esp_deep_sleep_start();
    // Non si torna qui: il risveglio riparte da un reset (nuovo app_main()).
}
