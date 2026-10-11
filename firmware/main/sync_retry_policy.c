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
