// Flusso di un ciclo (spec Fase 1a §4): boot base -> modalita' -> [CAPTURE] -> SYNC ->
// display stato -> deep sleep. In DEV resta sveglio con push/status server.
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"
#include "nvs_flash.h"

#include "config.h"
#include "fw_version.h"
#include "display.h"
#include "power.h"
#include "storage.h"
#include "audio.h"
#include "sensors.h"
#include "wifi.h"
#include "ota.h"
#include "capture.h"
#include "queue.h"
#include "capture_name.h"
#include "timesync.h"
#include "sync.h"
#include "status_http.h"

static const char *TAG = "app";

typedef struct {
    int   battery_pct;      // -1 se non letta
    float battery_v;
    bool  sd_ok;
    bool  wifi_ok;
    int   sent, rejected, remaining;
    bool  server_error;
    char  capture_msg[32];  // "Salvato 0:07" | "Scartato" | "Max 10:00" | "SD assente" | ...
} cycle_state_t;

static void nvs_init_early(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) ESP_LOGE(TAG, "nvs init: %s", esp_err_to_name(err));
}

// display_init + conferma anti-rollback (self-check: boot + display ok), una volta sola.
static void ensure_display(void)
{
    static bool done = false;
    if (done) return;
    done = true;
    ESP_ERROR_CHECK(display_init());
    ota_mark_valid_if_pending();
}

static void fmt_mmss(uint32_t ms, char *out, size_t n)
{
    uint32_t s = ms / 1000;
    snprintf(out, n, "%lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
}

static void do_capture(cycle_state_t *st)
{
    if (st->battery_pct >= 0 && st->battery_pct < SB_BATTERY_MIN_RECORD_PCT) {
        strlcpy(st->capture_msg, "Batteria scarica", sizeof(st->capture_msg));
        ensure_display();
        return;
    }
    if (!st->sd_ok) { strlcpy(st->capture_msg, "SD assente", sizeof(st->capture_msg)); ensure_display(); return; }
    uint64_t free_b = 0;
    if (storage_free_bytes(&free_b) == ESP_OK && free_b < SB_SD_MIN_FREE_BYTES) {
        strlcpy(st->capture_msg, "SD piena", sizeof(st->capture_msg));
        ensure_display();
        return;
    }
    if (audio_init() != ESP_OK) { strlcpy(st->capture_msg, "Errore audio", sizeof(st->capture_msg)); ensure_display(); return; }

    char part[QUEUE_PATH_MAX + 32], id[CAPTURE_NAME_MAX + 8];
    if (queue_new_part_path(part, sizeof(part), id, sizeof(id)) != ESP_OK ||
        capture_start(part) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
        ensure_display();
        return;
    }
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS + SB_CAPTURE_MAX_MS); // §4: registrazione esclusa dai 5 min
    ensure_display();            // ~1.2 s: il task registratore intanto scrive
    display_text("* REC", id);   // ~2 s bloccanti, idem

    int released = 0;
    while (released < 2 && capture_is_running()) {
        vTaskDelay(pdMS_TO_TICKS(50));
        released = power_pwr_pressed() ? 0 : released + 1;
    }
    capture_result_t r;
    capture_stop(&r);
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS);

    char mmss[16];
    fmt_mmss(r.duration_ms, mmss, sizeof(mmss));
    if (r.duration_ms < SB_CAPTURE_MIN_MS) {
        queue_discard(part);
        strlcpy(st->capture_msg, "Scartato", sizeof(st->capture_msg));
    } else if (queue_commit(part) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
    } else if (r.hit_max) {
        snprintf(st->capture_msg, sizeof(st->capture_msg), "Max %s", mmss);
    } else {
        snprintf(st->capture_msg, sizeof(st->capture_msg), "Salvato %s", mmss);
    }
    if (r.err != ESP_OK) ESP_LOGW(TAG, "cattura con errore: %s", esp_err_to_name(r.err));
    ESP_LOGI(TAG, "cattura: %s (%s)", st->capture_msg, id);
}

static void do_sync(cycle_state_t *st)
{
    if (st->battery_pct >= 0 && st->battery_pct < SB_BATTERY_MIN_RECORD_PCT) {
        ESP_LOGW(TAG, "batteria %d%%: niente Wi-Fi", st->battery_pct);
        return;
    }
    if (wifi_connect(SB_WIFI_BUDGET_MS) != ESP_OK) { ESP_LOGW(TAG, "no wifi"); return; }
    st->wifi_ok = true;
    ESP_LOGI(TAG, "rete: %s, server: %s", wifi_current_ssid(), wifi_server_base_url());

    timesync_run();

    if (st->sd_ok) {
        sync_result_t r;
        sync_run(SB_SYNC_WINDOW_MS, &r);
        st->sent = r.sent; st->rejected = r.rejected; st->remaining = r.remaining; st->server_error = r.server_error;
    }

    if (st->battery_pct < 0 || st->battery_pct >= SB_BATTERY_MIN_OTA_PCT) {
        char manifest_url[192];
        snprintf(manifest_url, sizeof(manifest_url), "%s/firmware/manifest.json", wifi_server_base_url());
        esp_err_t r = ota_pull(manifest_url);   // non ritorna se aggiorna
        ESP_LOGI(TAG, "ota_pull -> %s", esp_err_to_name(r));
    }
}

// Riga 1: ora locale a sinistra e versione firmware a destra (24 colonne da 8 px).
static void fmt_time_and_version(char *out, size_t n)
{
    char clock[8] = "--:--";
    time_t now = time(NULL); struct tm lt; struct tm utc;
    gmtime_r(&now, &utc); localtime_r(&now, &lt);
    if (capture_name_rtc_valid(&utc)) snprintf(clock, sizeof(clock), "%02d:%02d", lt.tm_hour, lt.tm_min);
    char ver[16];
    snprintf(ver, sizeof(ver), "v%s", fw_version());
    int pad = 24 - (int)strlen(clock) - (int)strlen(ver);
    if (pad < 1) pad = 1;
    snprintf(out, n, "%s%*s%s", clock, pad, "", ver);
}

static void fmt_wifi_line(bool wifi_ok, char *out, size_t n)
{
    if (wifi_ok && wifi_current_ssid()) snprintf(out, n, "wifi: %.18s", wifi_current_ssid());
    else strlcpy(out, "wifi: assente", n);
}

static void show_status(const cycle_state_t *st)
{
    char l1[32], l2[32], l3[32], l4[48];
    fmt_time_and_version(l1, sizeof(l1));
    fmt_wifi_line(st->wifi_ok, l2, sizeof(l2));
    if (st->battery_pct >= 0) snprintf(l3, sizeof(l3), "coda: %d  bat %d%%", st->remaining, st->battery_pct);
    else snprintf(l3, sizeof(l3), "coda: %d", st->remaining);
    if (st->capture_msg[0] && !st->wifi_ok) snprintf(l4, sizeof(l4), "%s / no sync", st->capture_msg);
    else if (!st->wifi_ok) strlcpy(l4, "no sync", sizeof(l4));
    else if (st->server_error) strlcpy(l4, "server ko", sizeof(l4));
    else if (st->sent > 0) snprintf(l4, sizeof(l4), "%d inviate", st->sent);
    else if (st->capture_msg[0]) strlcpy(l4, st->capture_msg, sizeof(l4));
    else strlcpy(l4, "sync ok", sizeof(l4));
    ESP_LOGI(TAG, "stato: [%s] [%s] [%s] [%s]", l1, l2, l3, l4);
    const char *const lines[4] = { l1, l2, l3, l4 };
    display_lines_n(lines, 4);
}

static void dev_mode(void)
{
    power_cycle_deadline_cancel();
    char ver[16], wl[32], ip[16] = "no wifi";
    snprintf(ver, sizeof(ver), "v%s", fw_version());
    bool ok = wifi_connect(SB_WIFI_BUDGET_MS) == ESP_OK;
    if (!ok) {
        ESP_LOGW(TAG, "DEV senza Wi-Fi");
    } else {
        wifi_get_ip(ip, sizeof(ip));
        if (ota_dev_server_start() == ESP_OK) {
            status_http_register();
            ESP_LOGI(TAG, "DEV OTA ready: curl --data-binary @build/secondbrain_fw.bin http://%s/ota", ip);
        }
    }
    fmt_wifi_line(ok, wl, sizeof(wl));
    const char *const lines[4] = { "DEV MODE", ip, wl, ver };
    display_lines_n(lines, 4);
    // Resta sveglio. Una pressione di PWR (2 campioni consecutivi a 100 ms) riavvia
    // in NORMAL: e' l'unico modo di uscire dal DEV mode senza togliere alimentazione.
    int pressed = 0, ticks = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(100));
        pressed = power_pwr_pressed() ? pressed + 1 : 0;
        if (pressed >= 2) {
            ESP_LOGI(TAG, "PWR premuto in DEV mode: riavvio in NORMAL");
            display_text("DEV MODE", "riavvio...");
            esp_restart();
        }
        if (++ticks % 50 == 0) ESP_LOGI(TAG, "alive (DEV mode, no sleep)");
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());
    setenv("TZ", SB_TZ, 1); tzset();

    // --- boot base ---
    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", power_boot_mode_name(mode));
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS);
    nvs_init_early();
    // In CAPTURE il display (~1.2 s di init) viene acceso DOPO l'avvio della
    // registrazione, per ridurre la latenza tra pressione e primo campione (§5.1).
    if (mode != BOOT_CAPTURE) ensure_display();

    if (mode == BOOT_DEV) {
        display_text("secondbrain", "DEV MODE");
        dev_mode();                        // non ritorna
    }

    cycle_state_t st = { .battery_pct = -1, .remaining = 0 };
    if (sensors_init() == ESP_OK) {
        if (sensors_read_battery(&st.battery_v, &st.battery_pct) != ESP_OK) st.battery_pct = -1;
        timesync_load_rtc();
    }
    ESP_LOGI(TAG, "batteria: %.2fV (%d%%)", st.battery_v, st.battery_pct);

    st.sd_ok = (storage_mount() == ESP_OK) && (queue_init() == ESP_OK);
    if (!st.sd_ok) ESP_LOGW(TAG, "SD non disponibile");

    if (mode == BOOT_CAPTURE) do_capture(&st);
    // In SYNC_ONLY nessuna schermata intermedia: un solo refresh e-Paper a fine ciclo
    // (ogni refresh completo "lampeggia" per ~2 s).

    do_sync(&st);
    if (st.sd_ok) st.remaining = queue_count(NULL);

    show_status(&st);
    vTaskDelay(pdMS_TO_TICKS(300));
    power_deep_sleep();
}
