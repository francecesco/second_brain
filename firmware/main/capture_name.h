#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

// Nomi delle catture (spec §5.3). Funzioni pure, testate su host.
// L'id cattura e' il nome SENZA estensione; il file e' "<nome>.wav".
#define CAPTURE_NAME_MAX 32

bool capture_name_rtc_valid(const struct tm *utc);                          // anno >= SB_RTC_MIN_VALID_YEAR
void capture_name_from_tm(const struct tm *utc, char *out, size_t n);       // cap_YYYYMMDD_HHMMSS (UTC)
void capture_name_unsynced(uint32_t seq, char *out, size_t n);              // cap_unsynced_NNNNNN
void capture_name_with_suffix(const char *base, int k, char *out, size_t n); // <base>_<k>
