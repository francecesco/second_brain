#include "capture_name.h"
#include "config.h"
#include <stdio.h>

bool capture_name_rtc_valid(const struct tm *utc)
{
    return utc && (utc->tm_year + 1900) >= SB_RTC_MIN_VALID_YEAR;
}

void capture_name_from_tm(const struct tm *utc, char *out, size_t n)
{
    snprintf(out, n, "cap_%04d%02d%02d_%02d%02d%02d",
             utc->tm_year + 1900, utc->tm_mon + 1, utc->tm_mday,
             utc->tm_hour, utc->tm_min, utc->tm_sec);
}

void capture_name_unsynced(uint32_t seq, char *out, size_t n)
{
    snprintf(out, n, "cap_unsynced_%06lu", (unsigned long)seq);
}

void capture_name_with_suffix(const char *base, int k, char *out, size_t n)
{
    snprintf(out, n, "%s_%d", base, k);
}
