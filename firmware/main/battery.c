#include "battery.h"
#include "sensors.h"
#include "config.h"
#include <stdio.h>
#include <time.h>
#include "esp_attr.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "battery";

// Lettura del ciclo precedente, conservata attraverso il deep sleep (persa se manca corrente).
RTC_DATA_ATTR static int s_prev_mv;
RTC_DATA_ATTR static int64_t s_prev_epoch;

static power_state_t s_last = { .valid = false, .source = POWER_SRC_USB, .mv = 0, .pct = -1 };

esp_err_t battery_read(power_state_t *out, bool remember)
{
    power_state_t st = { .valid = false, .source = POWER_SRC_USB, .mv = 0, .pct = -1 };
    int sum = 0, n = 0, mn = 100000, mx = 0;
    esp_err_t err = ESP_FAIL;
    for (int i = 0; i < SB_BATTERY_SAMPLES; i++) {
        float v = 0; int unused = 0;
        if (sensors_read_battery(&v, &unused) == ESP_OK) {
            int mv = (int)(v * 1000.0f + 0.5f);
            sum += mv; n++;
            if (mv < mn) mn = mv;
            if (mv > mx) mx = mv;
            err = ESP_OK;
        }
        if (i < SB_BATTERY_SAMPLES - 1) vTaskDelay(pdMS_TO_TICKS(5));
    }
    if (err == ESP_OK) {
        st.valid = true;
        st.mv = sum / n;
        int64_t now = (int64_t)time(NULL);
        int32_t dt = (s_prev_epoch > 0 && now > s_prev_epoch) ? (int32_t)(now - s_prev_epoch) : 0;
        bool usb_host = usb_serial_jtag_is_connected();
        st.source = battery_guess_source(st.mv, mn, mx, s_prev_mv, dt, usb_host);
        st.pct = st.source == POWER_SRC_BATTERY ? battery_pct_from_mv(st.mv) : -1;
        ESP_LOGI(TAG, "%d mV [%d..%d] (prec. %d mV, %ld s fa), usb_host=%d -> %s %d%%",
                 st.mv, mn, mx, s_prev_mv, (long)dt, usb_host,
                 st.source == POWER_SRC_USB ? "USB" : "batteria", st.pct);
        if (remember) { s_prev_mv = st.mv; s_prev_epoch = now; }
    } else {
        ESP_LOGW(TAG, "lettura fallita: %s", esp_err_to_name(err));
    }
    s_last = st;
    if (out) *out = st;
    return err;
}

const power_state_t *battery_last(void) { return &s_last; }

bool battery_below(int pct)
{
    return s_last.valid && s_last.source == POWER_SRC_BATTERY && s_last.pct < pct;
}

void battery_label(char *out, int n)
{
    if (!s_last.valid) snprintf(out, n, "bat ?");
    else if (s_last.source == POWER_SRC_USB) snprintf(out, n, "USB");
    else snprintf(out, n, "bat %d%%", s_last.pct);
}
