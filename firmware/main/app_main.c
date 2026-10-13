// Flusso di un ciclo (spec Fase 1a §4): boot base -> modalita' -> [CAPTURE] -> SYNC ->
// display stato -> deep sleep. In DEV resta sveglio con push/status server.
//
// Latenza (2026-09-24): in CAPTURE contano i millisecondi tra pressione e primo campione.
// Per questo l'init dell'e-Paper (~1.2 s, quasi solo attese su BUSY) parte in un task
// parallelo appena il device si sveglia, e l'init audio (~0.3 s, I2C/I2S) corre in un
// task parallelo al mount della SD (SDMMC). Il main aspetta ("join") solo quando serve.
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
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
#include "secrets.h"
#include "sync_retry_policy.h"
#include "retry_state.h"
#include "ota.h"
#include "capture.h"
#include "queue.h"
#include "capture_name.h"
#include "timesync.h"
#include "sync.h"
#include "status_http.h"
#include "diag.h"
#include "battery.h"
#include "buttons.h"
#include "button_policy.h"
#include "digest.h"
#include "esp_http_client.h"

static const char *TAG = "app";

typedef struct {
    bool  sd_ok;
    bool  wifi_ok;
    int   sent, rejected, remaining;
    bool  server_error;
    bool  auth_error;
    bool  server_unreachable; // l'OTA pull non ha potuto connettersi: inutile riprovare con l'immagine Oggi
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

// ---------------------------------------------------------------------------
// Init paralleli: display e audio in task dedicati, con join esplicito.
// ---------------------------------------------------------------------------
static SemaphoreHandle_t s_display_done = NULL;
static esp_err_t s_display_err = ESP_FAIL;
static SemaphoreHandle_t s_audio_done = NULL;
static esp_err_t s_audio_err = ESP_FAIL;

static void display_init_task(void *arg)
{
    (void)arg;
    s_display_err = display_init();
    xSemaphoreGive(s_display_done);
    vTaskDelete(NULL);
}

static void audio_init_task(void *arg)
{
    (void)arg;
    s_audio_err = audio_init();
    xSemaphoreGive(s_audio_done);
    vTaskDelete(NULL);
}

static void display_init_async(void)
{
    s_display_done = xSemaphoreCreateBinary();
    if (!s_display_done || xTaskCreate(display_init_task, "epd_init", 4096, NULL, tskIDLE_PRIORITY + 2, NULL) != pdPASS) {
        s_display_err = display_init(); // fallback sincrono
        if (s_display_done) xSemaphoreGive(s_display_done);
    }
}

// Attende l'init del display (una volta sola) e conferma l'immagine OTA (self-check).
static void ensure_display(void)
{
    static bool done = false;
    if (done) return;
    done = true;
    if (s_display_done) xSemaphoreTake(s_display_done, pdMS_TO_TICKS(5000));
    if (s_display_err != ESP_OK) {
        ESP_LOGE(TAG, "display_init fallita: %s", esp_err_to_name(s_display_err));
        // Senza display si prosegue comunque: la cattura vale piu' della schermata.
    }
    ota_mark_valid_if_pending();
}

static void audio_init_async(void)
{
    s_audio_done = xSemaphoreCreateBinary();
    if (!s_audio_done || xTaskCreate(audio_init_task, "audio_init", 4096, NULL, tskIDLE_PRIORITY + 3, NULL) != pdPASS) {
        s_audio_err = audio_init();
        if (s_audio_done) xSemaphoreGive(s_audio_done);
    }
}

static esp_err_t audio_init_join(void)
{
    if (s_audio_done) xSemaphoreTake(s_audio_done, pdMS_TO_TICKS(5000));
    return s_audio_err;
}

// ---------------------------------------------------------------------------
static void fmt_mmss(uint32_t ms, char *out, size_t n)
{
    uint32_t s = ms / 1000;
    snprintf(out, n, "%lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
}

// Registrazione a click (spec tasti §3). Ritorna true se un click in piu' nei primi
// SB_DEV_CLICK_WINDOW_MS chiede la DEV MODE: la registrazione appena partita e' scartata.
static bool do_capture(cycle_state_t *st)
{
    diag_t *d = diag_get();
    if (battery_below(SB_BATTERY_MIN_RECORD_PCT)) {
        strlcpy(st->capture_msg, "Batteria scarica", sizeof(st->capture_msg));
        return false;
    }
    if (!st->sd_ok) { strlcpy(st->capture_msg, "SD assente", sizeof(st->capture_msg)); return false; }
    uint64_t free_b = 0;
    if (storage_free_bytes(&free_b) == ESP_OK && free_b < SB_SD_MIN_FREE_BYTES) {
        strlcpy(st->capture_msg, "SD piena", sizeof(st->capture_msg));
        return false;
    }
    if (audio_init_join() != ESP_OK) { strlcpy(st->capture_msg, "Errore audio", sizeof(st->capture_msg)); return false; }

    char part[QUEUE_PATH_MAX + 32], id[CAPTURE_NAME_MAX + 8];
    if (queue_new_part_path(part, sizeof(part), id, sizeof(id)) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
        return false;
    }
    // Beep PRIMA di aprire la registrazione: non finisce nel file e dice "parla ora".
    // L'amplificatore resta acceso per tutta la cattura (i beep non aspettano l'avvio).
    audio_amp_enable(true);
    audio_beep(SB_BEEP_FREQ_HZ, SB_BEEP_START_MS);
    if (capture_start(part) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
        return false;
    }
    d->t_capture_start = diag_now_ms();
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS + SB_CAPTURE_MAX_MS); // §4: registrazione esclusa dai 5 min

    ensure_display();            // il task registratore intanto scrive
    display_text("* REC", id);   // ~2 s di refresh, idem
    d->t_rec_shown = diag_now_ms();

    // I click li annota il task dei tasti, anche mentre qui si aspettava il refresh: il primo
    // dopo quello di avvio decide. Senza click la registrazione finisce da sola a 10 min.
    bool dev = false;
    while (capture_is_running()) {
        if (buttons_clicks() > 0) {
            dev = button_capture_gesture(buttons_click_ms(0)) == GESTURE_DEV;
            ESP_LOGI(TAG, "click di PWR a %lu ms dall'avvio -> %s", (unsigned long)buttons_click_ms(0), dev ? "DEV" : "stop");
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    capture_result_t r;
    capture_stop(&r);
    if (dev) {
        queue_discard(part);
        audio_amp_enable(false);
        return true;
    }
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS);
    d->capture_ms = r.duration_ms;
    d->peak = r.peak;

    char mmss[16];
    fmt_mmss(r.duration_ms, mmss, sizeof(mmss));
    if (r.duration_ms < SB_CAPTURE_MIN_MS) {
        queue_discard(part);
        strlcpy(st->capture_msg, "Scartato", sizeof(st->capture_msg));
        display_text_async("Scartato", "");
    } else if (queue_commit(part) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
    } else {
        if (r.hit_max) snprintf(st->capture_msg, sizeof(st->capture_msg), "Max %s", mmss);
        else snprintf(st->capture_msg, sizeof(st->capture_msg), "Salvato %s", mmss);
        // Feedback immediato: beep, poi schermata "Salvato" in parallelo (il main passa al Wi-Fi).
        audio_beep(SB_BEEP_FREQ_HZ, SB_BEEP_STOP_MS);   // un beep: salvato
        display_text_async(st->capture_msg, id);
    }
    audio_amp_enable(false);
    if (r.err != ESP_OK) ESP_LOGW(TAG, "cattura con errore: %s", esp_err_to_name(r.err));
    ESP_LOGI(TAG, "cattura: %s (%s)", st->capture_msg, id);
    return false;
}

static void do_sync(cycle_state_t *st)
{
    if (battery_below(SB_BATTERY_MIN_RECORD_PCT)) {
        ESP_LOGW(TAG, "batteria %d%%: niente Wi-Fi", battery_last()->pct);
        return;
    }
    if (wifi_connect(SB_WIFI_BUDGET_MS) != ESP_OK) { ESP_LOGW(TAG, "no wifi"); return; }
    st->wifi_ok = true;
    diag_get()->t_wifi_connected = diag_now_ms();
    ESP_LOGI(TAG, "rete: %s, server: %s", wifi_current_ssid(), SERVER_BASE_URL);

    timesync_run();

    if (st->sd_ok) {
        sync_result_t r;
        sync_run(SB_SYNC_WINDOW_MS, &r);
        st->sent = r.sent; st->rejected = r.rejected; st->remaining = r.remaining; st->server_error = r.server_error; st->auth_error = r.auth_error;
    }

    if (!battery_below(SB_BATTERY_MIN_OTA_PCT)) {
        char manifest_url[192];
        snprintf(manifest_url, sizeof(manifest_url), "%s/firmware/manifest.json", SERVER_BASE_URL);
        esp_err_t r = ota_pull(manifest_url);   // non ritorna se aggiorna
        ESP_LOGI(TAG, "ota_pull -> %s", esp_err_to_name(r));
        if (r == ESP_ERR_HTTP_CONNECT) st->server_unreachable = true;   // una risposta HTTP, anche d'errore, no
        if (r == ESP_ERR_NOT_ALLOWED) st->auth_error = true; // token rifiutato anche a coda vuota: "token ko"
    }
}

// Ora locale in alto a sinistra ("--:--" se l'RTC non e' valido). La versione del
// firmware compare solo nella schermata DEV.
static void fmt_clock(char *out, size_t n)
{
    time_t now = time(NULL); struct tm lt; struct tm utc;
    gmtime_r(&now, &utc); localtime_r(&now, &lt);
    if (capture_name_rtc_valid(&utc)) snprintf(out, n, "%02d:%02d", lt.tm_hour, lt.tm_min);
    else strlcpy(out, "--:--", n);
}

// Icona della batteria in alto a destra.
static screen_battery_t screen_battery(const power_state_t *ps)
{
    if (!ps->valid) return SCREEN_BAT_UNKNOWN;
    switch (ps->charge) {
    case CHARGE_DISCHARGING: return SCREEN_BAT_DISCHARGING;
    case CHARGE_CHARGING:    return SCREEN_BAT_CHARGING;
    case CHARGE_FULL:        return SCREEN_BAT_FULL;
    default:                 return SCREEN_BAT_NONE;
    }
}

static void fmt_wifi_line(bool wifi_ok, char *out, size_t n)
{
    if (wifi_ok && wifi_current_ssid()) snprintf(out, n, "wifi: %.18s", wifi_current_ssid());
    else strlcpy(out, "wifi: assente", n);
}

// Con digest (immagine della schermata "Oggi") disegna quella con il piede di stato,
// altrimenti la schermata di stato classica.
static void show_status(const cycle_state_t *st, const uint8_t *digest)
{
    char l1[8], l2[32], l3[32], l4[48];
    fmt_clock(l1, sizeof(l1));
    fmt_wifi_line(st->wifi_ok, l2, sizeof(l2));
    char bl[20];
    battery_label(bl, sizeof(bl));
    snprintf(l3, sizeof(l3), "coda: %d  %s", st->remaining, bl);
    // SD assente (alert): spiega la fascia nera al posto di "sync ok"; i guasti di rete e i
    // messaggi di cattura hanno la precedenza perche' dicono di piu' sul ciclo
    // Senza rete l'esito della cattura vince su "no sync": "nessuna rete" e' gia' sopra.
    if (st->capture_msg[0] && !st->wifi_ok) strlcpy(l4, st->capture_msg, sizeof(l4));
    else if (!st->wifi_ok) strlcpy(l4, st->sd_ok ? "no sync" : "SD assente", sizeof(l4));
    else if (st->auth_error) strlcpy(l4, "token ko", sizeof(l4));
    else if (st->server_error) strlcpy(l4, "server ko", sizeof(l4));
    else if (st->sent > 0) snprintf(l4, sizeof(l4), "%d inviate", st->sent);
    else if (st->capture_msg[0]) strlcpy(l4, st->capture_msg, sizeof(l4));
    else if (!st->sd_ok) strlcpy(l4, "SD assente", sizeof(l4));
    else strlcpy(l4, "sync ok", sizeof(l4));
    ESP_LOGI(TAG, "stato: [%s] [%s] [%s] [%s]", l1, l2, l3, l4);
    const power_state_t *ps = battery_last();
    const screen_status_t scr = {
        .clock = l1,
        .battery = screen_battery(ps),
        .pct = ps->valid ? ps->pct : -1,
        .network = st->wifi_ok ? wifi_current_ssid() : NULL,
        .queue = st->remaining,
        .outcome = l4,
        .alert = !st->wifi_ok || st->auth_error || st->server_error || !st->sd_ok,
    };
    ensure_display();
    if (digest) display_today(&scr, digest);
    else display_status(&scr);
}

// Immagine della schermata "Oggi" per questo ciclo (spec Home Assistant): quella sulla SD, o
// una nuova dal backend se c'e' rete e batteria per scaricarla. Ritorna true se buf contiene
// un'immagine da mostrare; etag_now e' allora il suo etag. Un 401/403 qui non tocca coda ne'
// esito: il token rifiutato lo segnala gia' l'OTA pull.
static bool load_today(const cycle_state_t *st, uint8_t *buf, char *etag_now, size_t etag_len)
{
    bool cached = st->sd_ok && digest_load(buf, etag_now, etag_len) == ESP_OK;
    if (!cached) etag_now[0] = '\0';
    if (!digest_should_fetch(st->wifi_ok, !battery_below(SB_BATTERY_MIN_OTA_PCT), st->server_unreachable)) {
        ESP_LOGI(TAG, "digest: skip (%s) etag=%s",
                 !st->wifi_ok ? "no wifi" : st->server_unreachable ? "server irraggiungibile" : "batteria",
                 cached ? etag_now : "-");
        return cached;
    }
    char etag_new[SB_DIGEST_ETAG_MAX];
    digest_fetch_t r = digest_fetch(etag_now, buf, etag_new, sizeof(etag_new));
    bool has = cached;
    switch (r) {
    case DIGEST_NEW:
        if (st->sd_ok && digest_save(buf, etag_new) != ESP_OK) ESP_LOGW(TAG, "digest: salvataggio su SD fallito");
        strlcpy(etag_now, etag_new, etag_len);
        has = true;
        break;
    case DIGEST_SAME:
        break;
    case DIGEST_NONE:
        if (st->sd_ok) digest_clear();
        has = false;
        break;
    case DIGEST_FAILED:
        // digest_fetch puo' aver scritto in buf: l'immagine della SD va riletta.
        has = cached && digest_load(buf, etag_now, etag_len) == ESP_OK;
        break;
    }
    if (!has) etag_now[0] = '\0';
    ESP_LOGI(TAG, "digest: %s etag=%s", digest_fetch_name(r), has ? etag_now : "-");
    return has;
}

// Esito della riga 4 come categoria, per decidere se un wake da timer deve aggiornare lo schermo.
static status_outcome_t cycle_outcome(const cycle_state_t *st)
{
    if (!st->wifi_ok) return STATUS_OUTCOME_NO_WIFI;
    if (st->auth_error) return STATUS_OUTCOME_AUTH_KO;
    if (st->server_error) return STATUS_OUTCOME_SERVER_KO;
    if (st->sent > 0) return STATUS_OUTCOME_SENT;
    return STATUS_OUTCOME_OK;
}

// Quello che la schermata di stato mostra, ora esclusa (spec wake periodico §3.3).
static status_view_t cycle_view(const cycle_state_t *st)
{
    status_view_t v = { .outcome = cycle_outcome(st), .remaining = st->remaining };
    if (st->wifi_ok && wifi_current_ssid()) strlcpy(v.ssid, wifi_current_ssid(), sizeof(v.ssid));
    const power_state_t *ps = battery_last();
    v.charge = ps->valid ? ps->charge : CHARGE_NONE;
    v.pct = ps->valid ? ps->pct : -1;
    return v;
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
    ensure_display();
    const char *const lines[4] = { "DEV MODE", ip, wl, ver };
    display_lines_n(lines, 4);

    // Resta sveglio. Un click di USER (2 campioni consecutivi a 100 ms) riavvia in NORMAL:
    // non PWR, cosi' un click in piu' del triplo click non chiude subito la DEV MODE.
    int pressed = 0, ticks = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(100));
        pressed = power_user_pressed() ? pressed + 1 : 0;
        if (pressed >= 2) {
            ESP_LOGI(TAG, "USER premuto in DEV mode: riavvio in NORMAL");
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
    if (mode == BOOT_CAPTURE) buttons_watch_start();   // subito: i click del triplo click sono rapidi
    ESP_LOGI(TAG, "boot mode: %s", power_boot_mode_name(mode));
    if (mode != BOOT_DEV) diag_begin(power_boot_mode_name(mode)); // in DEV si conserva l'ultimo ciclo reale, per leggerlo da /status
    display_init_async();                  // ~1.2 s di attese su BUSY: corre da solo
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS);
    nvs_init_early();

    if (mode == BOOT_DEV) {
        dev_mode();                        // non ritorna
    }

    cycle_state_t st = { .remaining = 0 };
    if (sensors_init() == ESP_OK) {
        timesync_load_rtc();               // I2C + RTC: serve per il nome file
        battery_read(NULL, true);          // ADC, < 1 ms: serve per la soglia di registrazione
    }

    if (mode == BOOT_CAPTURE) audio_init_async();        // I2C/I2S in parallelo al mount SD

    st.sd_ok = (storage_mount() == ESP_OK) && (queue_init() == ESP_OK);
    if (!st.sd_ok) ESP_LOGW(TAG, "SD non disponibile");

    if (mode == BOOT_CAPTURE && do_capture(&st)) {
        ESP_LOGI(TAG, "triplo click: DEV MODE");
        dev_mode();                        // non ritorna
    }


    if (mode == BOOT_TIMER_SYNC) ESP_LOGI(TAG, "wake dal timer");
    do_sync(&st);
    if (st.sd_ok) st.remaining = queue_count(NULL);

    // Immagine della schermata "Oggi" (SB_DIGEST_BYTES: sull'heap).
    uint8_t *digest = malloc(SB_DIGEST_BYTES);
    char etag_now[SB_DIGEST_ETAG_MAX] = "";
    bool has_digest = digest && load_today(&st, digest, etag_now, sizeof(etag_now));
    if (!digest) ESP_LOGE(TAG, "malloc immagine Oggi fallita: schermata di stato");

    status_view_t view = cycle_view(&st);
    bool refresh = (mode != BOOT_TIMER_SYNC) ||
                   today_should_refresh(status_should_refresh(st.sent, &view, retry_state_shown()),
                                        has_digest ? etag_now : NULL, retry_state_digest_shown());
    if (refresh) {
        show_status(&st, has_digest ? digest : NULL);
        retry_state_set_shown(&view);
        retry_state_set_digest_shown(has_digest ? etag_now : NULL);
    } else {
        ESP_LOGI(TAG, "wake dal timer senza novita' (esito %d, coda %d, carica %d, %d%%): display non aggiornato",
                 (int)view.outcome, view.remaining, (int)view.charge, view.pct);
    }
    free(digest);

    // Wake periodico (spec wake periodico §3): ogni SB_WAKE_PERIOD_MS, a batteria non sotto
    // la soglia di sync; altrimenti solo da tasto.
    const power_state_t *ps = battery_last();
    uint32_t timer_ms = 0;
    if (wake_timer_should_arm(ps->valid && ps->source == POWER_SRC_BATTERY, ps->pct)) timer_ms = SB_WAKE_PERIOD_MS;
    diag_get()->next_wake_s = timer_ms / 1000;
    diag_get()->t_cycle_end = diag_now_ms();
    vTaskDelay(pdMS_TO_TICKS(300));
    power_deep_sleep(timer_ms);
}
