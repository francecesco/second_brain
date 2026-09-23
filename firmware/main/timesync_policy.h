#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <time.h>
// Policy di sincronizzazione ora (spec §6.4) e conversione tm(UTC)->epoch. Pure.
bool timesync_needed(int rtc_year, int64_t last_ntp_epoch, int64_t now_epoch);
int64_t timesync_tm_to_epoch_utc(const struct tm *utc);
