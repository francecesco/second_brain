#include "sync_retry_policy.h"
#include "config.h"
#include <stdlib.h>
#include <string.h>

bool wake_timer_should_arm(bool on_battery, int battery_pct)
{
    return !(on_battery && battery_pct < SB_BATTERY_MIN_RECORD_PCT);
}

bool status_should_refresh(int sent, const status_view_t *now, const status_view_t *shown)
{
    if (sent > 0) return true;
    if (now->outcome != shown->outcome || now->remaining != shown->remaining ||
        now->charge != shown->charge || strcmp(now->ssid, shown->ssid) != 0) {
        return true;
    }
    if ((now->pct < 0) != (shown->pct < 0)) return true;
    return abs(now->pct - shown->pct) >= SB_BATTERY_DISPLAY_STEP_PCT;
}

bool today_should_refresh(bool status_changed, const char *etag_now, const char *etag_shown)
{
    if (status_changed) return true;
    return strcmp(etag_now ? etag_now : "", etag_shown ? etag_shown : "") != 0;
}

bool digest_size_valid(long size)
{
    return size == SB_DIGEST_BYTES;
}

bool digest_etag_storable(const char *etag)
{
    return etag && strlen(etag) < SB_DIGEST_ETAG_MAX;
}

bool digest_should_fetch(bool wifi_ok, bool battery_ok, bool server_unreachable)
{
    return wifi_ok && battery_ok && !server_unreachable;
}
