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

// Tasti e modalita' di boot (spec Fase 1a §4.2).
//
// BOARD_BTN_PWR (GPIO18) e' l'unico wake source dal deep sleep (RTC-capable, non
// strapping): tenerlo premuto durante il reset e' sicuro. Se resta premuto per
// tutti i primi 300 ms -> CAPTURE (registra finche' e' tenuto).
//
// BOARD_BTN_USER e' GPIO0, pin di strapping BOOT: se la ROM lo trova basso al
// reset/wake il chip entra in download mode. Quindi NON va premuto nell'istante
// del wake, ma SUBITO DOPO: la finestra per il DEV mode e' 1 s dall'avvio
// dell'app (gesto: premi PWR, rilascia, premi USER).
#define POWER_PWR_DEBOUNCE_SAMPLES 3
#define POWER_PWR_DEBOUNCE_STEP_MS 50   // ~150ms totali (3 x 50ms): basta per il debounce, e ogni ms
                                        // qui ritarda la registrazione

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

#define POWER_MODE_USER_WINDOW_US (1000 * 1000)  // finestra DEV per USER: 1 s dal boot

boot_mode_t power_boot_mode(void)
{
    // Fase 1: 6 campioni x 50 ms su entrambi i tasti.
    bool pwr_held_all = true;
    bool user_seen = false;
    for (int i = 0; i < POWER_PWR_DEBOUNCE_SAMPLES; i++) {
        if (power_user_pressed()) user_seen = true;
        if (!power_pwr_pressed()) pwr_held_all = false;
        vTaskDelay(pdMS_TO_TICKS(POWER_PWR_DEBOUNCE_STEP_MS));
    }
    if (user_seen) {
        ESP_LOGI(TAG, "boot-mode: USER premuto nei primi %d ms -> DEV", POWER_PWR_DEBOUNCE_SAMPLES * POWER_PWR_DEBOUNCE_STEP_MS);
        return BOOT_DEV;
    }
    if (pwr_held_all) {
        ESP_LOGI(TAG, "boot-mode: PWR tenuto su %d campioni -> CAPTURE", POWER_PWR_DEBOUNCE_SAMPLES);
        return BOOT_CAPTURE;
    }
    // Fase 2: PWR rilasciato (pressione breve o reset): finestra fino a 1 s per USER.
    while (esp_timer_get_time() < POWER_MODE_USER_WINDOW_US) {
        if (power_user_pressed()) {
            ESP_LOGI(TAG, "boot-mode: USER premuto entro 1s -> DEV");
            return BOOT_DEV;
        }
        vTaskDelay(pdMS_TO_TICKS(POWER_PWR_DEBOUNCE_STEP_MS));
    }
    ESP_LOGI(TAG, "boot-mode: nessun tasto tenuto -> SYNC_ONLY");
    return BOOT_SYNC_ONLY;
}

const char *power_boot_mode_name(boot_mode_t m)
{
    switch (m) {
    case BOOT_DEV:     return "DEV";
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
    power_deep_sleep();
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

// Wake da deep sleep: usiamo BOARD_BTN_PWR (GPIO18), NON BOARD_BTN_USER (GPIO0).
//
// GPIO0 e' il pin di strapping BOOT: se durante il risveglio/reset la ROM lo
// trova basso, il chip entra in download mode invece di rilanciare il nostro
// firmware. Tenere premuto un tasto collegato a GPIO0 per svegliare la scheda
// romperebbe quindi il boot normale. BOARD_BTN_PWR (GPIO18) e' RTC-capable
// sulla ESP32-S3 (RTC GPIO 0-21) e non e' un pin di strapping: e' la scelta
// sicura per un wake-source EXT1.
void power_deep_sleep(void)
{
    // In deep sleep il dominio digitale (e con esso la pull-up impostata da
    // gpio_config in power_init) e' spento: per mantenere il pin in idle alto
    // durante il sonno serve una pull-up nel dominio RTC.
    rtc_gpio_pullup_en(BOARD_BTN_PWR);
    rtc_gpio_pulldown_dis(BOARD_BTN_PWR);

    // Tasto attivo-basso -> wake quando il livello scende (EXT1, ANY_LOW).
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup(1ULL << BOARD_BTN_PWR,
                                                  ESP_EXT1_WAKEUP_ANY_LOW));

    // Il mantenimento batteria deve restare alto anche durante il sonno, altrimenti a
    // batteria il deep sleep diventerebbe uno spegnimento fisico.
    gpio_set_level(BOARD_BAT_LATCH, 1);
    gpio_hold_en(BOARD_BAT_LATCH);
    gpio_deep_sleep_hold_en();

    ESP_LOGI(TAG, "entering deep sleep; press PWR to wake");
    esp_deep_sleep_start();
    // Non si torna qui: il risveglio riparte da un reset (nuovo app_main()).
}
