#include "sync_retry_policy.h"
#include "config.h"

uint32_t sync_retry_delay_ms(unsigned attempt)
{
    uint64_t ms = SB_SYNC_RETRY_MIN_MS;
    for (unsigned i = 0; i < attempt && ms < SB_SYNC_RETRY_MAX_MS; i++) ms *= 2;
    return ms > SB_SYNC_RETRY_MAX_MS ? SB_SYNC_RETRY_MAX_MS : (uint32_t)ms;
}

bool sync_retry_should_arm(int remaining, bool on_battery, int battery_pct)
{
    if (remaining <= 0) return false;
    if (on_battery && battery_pct < SB_BATTERY_MIN_RECORD_PCT) return false;
    return true;
}

bool sync_retry_counts_as_failure(int sent, int remaining)
{
    return sent == 0 && remaining > 0;
}

bool status_should_refresh(int sent, status_outcome_t outcome, status_outcome_t shown)
{
    return sent > 0 || outcome != shown;
}
