#include "timesync_policy.h"
#include "config.h"

bool timesync_needed(int rtc_year, int64_t last_ntp_epoch, int64_t now_epoch)
{
    if (rtc_year < SB_RTC_MIN_VALID_YEAR) return true;
    if (last_ntp_epoch <= 0) return true;
    return (now_epoch - last_ntp_epoch) > SB_NTP_MAX_AGE_S;
}

// Howard Hinnant, days_from_civil: giorni dal 1970-01-01 per una data del calendario
// gregoriano proleptico. Evita mktime/timegm, che dipendono da TZ e dalla libc.
static int64_t days_from_civil(int y, unsigned m, unsigned d)
{
    y -= m <= 2;
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = (unsigned)(y - era * 400);
    const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + (int64_t)doe - 719468;
}

int64_t timesync_tm_to_epoch_utc(const struct tm *utc)
{
    int64_t days = days_from_civil(utc->tm_year + 1900, (unsigned)(utc->tm_mon + 1), (unsigned)utc->tm_mday);
    return days * 86400 + utc->tm_hour * 3600 + utc->tm_min * 60 + utc->tm_sec;
}
