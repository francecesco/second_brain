#include "battery_policy.h"
#include "config.h"

// Curva di scarica LiPo tipica (mV -> %). Da sostituire con quella misurata.
static const struct { int mv; int pct; } CURVE[] = {
    {3270, 0}, {3610, 5}, {3690, 10}, {3710, 15}, {3730, 20}, {3750, 25}, {3770, 30},
    {3790, 35}, {3800, 40}, {3820, 45}, {3840, 50}, {3850, 55}, {3870, 60}, {3910, 65},
    {3950, 70}, {3980, 75}, {4020, 80}, {4080, 85}, {4110, 90}, {4150, 95}, {4200, 100},
};
#define CURVE_N (int)(sizeof(CURVE) / sizeof(CURVE[0]))

int battery_pct_from_mv(int mv)
{
    if (mv <= CURVE[0].mv) return 0;
    if (mv >= CURVE[CURVE_N - 1].mv) return 100;
    for (int i = 1; i < CURVE_N; i++) {
        if (mv <= CURVE[i].mv) {
            int dmv = CURVE[i].mv - CURVE[i - 1].mv;
            int dp = CURVE[i].pct - CURVE[i - 1].pct;
            return CURVE[i - 1].pct + (mv - CURVE[i - 1].mv) * dp / dmv;
        }
    }
    return 100;
}

power_source_t battery_guess_source(int mv, int min_mv, int max_mv, int prev_mv, int32_t dt_s, bool usb_host)
{
    if (usb_host) return POWER_SRC_USB;
    // Acceso ma sotto la tensione minima di funzionamento: non puo' essere la batteria a
    // tenerlo in vita. Succede senza batteria: il caricabatterie fa impulsi di prova.
    if (min_mv < SB_BATTERY_MIN_RUNNING_MV) return POWER_SRC_USB;
    if (max_mv - min_mv > SB_BATTERY_MAX_SPREAD_MV) return POWER_SRC_USB;
    if (mv >= SB_USB_VOLTAGE_MV) return POWER_SRC_USB;
    if (prev_mv > 0 && dt_s > 0 && dt_s <= SB_CHARGE_TREND_MAX_AGE_S &&
        mv - prev_mv >= SB_CHARGE_RISE_MV) {
        return POWER_SRC_USB;
    }
    return POWER_SRC_BATTERY;
}
