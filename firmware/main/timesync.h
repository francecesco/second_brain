#pragma once
#include <stdbool.h>
#include "esp_err.h"
// Ora (spec §6.4): RTC PCF85063 (UTC) <-> ora di sistema; SNTP quando serve.
void      timesync_load_rtc(void);   // al boot: settimeofday dall'RTC, TZ=SB_TZ per il display
bool      timesync_rtc_valid(void);  // anno >= SB_RTC_MIN_VALID_YEAR
esp_err_t timesync_run(void);        // con Wi-Fi: SNTP se necessario -> RTC + NVS lastntp
