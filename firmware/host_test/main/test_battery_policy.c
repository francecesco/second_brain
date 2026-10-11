#include "unity.h"
#include "battery_policy.h"

void test_battery_pct_from_mv(void) {
    TEST_ASSERT_EQUAL_INT(100, battery_pct_from_mv(4250));
    TEST_ASSERT_EQUAL_INT(100, battery_pct_from_mv(4200));
    TEST_ASSERT_EQUAL_INT(0, battery_pct_from_mv(3200));
    TEST_ASSERT_EQUAL_INT(0, battery_pct_from_mv(3270));
    TEST_ASSERT_EQUAL_INT(50, battery_pct_from_mv(3840));
    // interpolazione lineare tra i punti della curva (3.84 V = 50 %, 3.85 V = 55 %)
    int p = battery_pct_from_mv(3845);
    TEST_ASSERT_TRUE(p > 50 && p < 55);
    // monotona
    int prev = -1;
    for (int mv = 3200; mv <= 4250; mv += 10) {
        int q = battery_pct_from_mv(mv);
        TEST_ASSERT_TRUE(q >= prev);
        prev = q;
    }
}

// campioni stabili: min = max = media
static power_source_t guess(int mv, int prev, int32_t dt, bool host) {
    return battery_guess_source(mv, mv, mv, prev, dt, host);
}

void test_battery_source_usb_host(void) {
    TEST_ASSERT_EQUAL_INT(POWER_SRC_USB, guess(3700, 3700, 600, true));
}

void test_battery_source_no_battery(void) {
    // Senza batteria il caricabatterie fa impulsi: letture basse o instabili => USB.
    TEST_ASSERT_EQUAL_INT(POWER_SRC_USB, battery_guess_source(2906, 2906, 2906, 0, 0, false));
    TEST_ASSERT_EQUAL_INT(POWER_SRC_USB, battery_guess_source(3900, 3000, 4180, 0, 0, false));
    TEST_ASSERT_EQUAL_INT(POWER_SRC_USB, battery_guess_source(3900, 3800, 4000, 0, 0, false));
    // piccola dispersione (rumore ADC): batteria
    TEST_ASSERT_EQUAL_INT(POWER_SRC_BATTERY, battery_guess_source(3900, 3870, 3930, 0, 0, false));
}

void test_battery_source_charger_voltage(void) {
    // senza batteria si leggevano 4140 mV grezzi, cioe' ~4250 corretti: USB
    TEST_ASSERT_EQUAL_INT(POWER_SRC_USB, guess(battery_mv_calibrated(4140), 0, 0, false));
    TEST_ASSERT_EQUAL_INT(POWER_SRC_BATTERY, guess(4000, 0, 0, false));
    // batteria piena appena staccata dalla USB (4,20 V): ancora batteria
    TEST_ASSERT_EQUAL_INT(POWER_SRC_BATTERY, guess(4200, 0, 0, false));
}

void test_battery_mv_calibrated(void) {
    // Taratura del 2026-10-11: il massimo letto in carica (4091 mV) sono i 4,20 V di fine carica.
    int full = battery_mv_calibrated(4091);
    TEST_ASSERT_TRUE(full >= 4190 && full <= 4210);
    TEST_ASSERT_EQUAL_INT(0, battery_mv_calibrated(0));
    // a fine carica, assestata (4059-4070 mV grezzi), conta come piena
    TEST_ASSERT_EQUAL_INT(CHARGE_FULL, battery_charge_state(POWER_SRC_USB, true, battery_mv_calibrated(4059)));
}

void test_battery_source_rising_means_charging(void) {
    // salita di 40 mV in 10 minuti: sta caricando
    TEST_ASSERT_EQUAL_INT(POWER_SRC_USB, guess(3840, 3800, 600, false));
    // salita piccola (recupero dopo un carico): batteria
    TEST_ASSERT_EQUAL_INT(POWER_SRC_BATTERY, guess(3815, 3800, 600, false));
    // lettura precedente troppo vecchia: non conta
    TEST_ASSERT_EQUAL_INT(POWER_SRC_BATTERY, guess(3840, 3800, 7200, false));
    // tensione in calo: batteria
    TEST_ASSERT_EQUAL_INT(POWER_SRC_BATTERY, guess(3790, 3800, 600, false));
}

void test_battery_present(void) {
    // Batteria vera su USB (misurata il 2026-10-11): campioni stabili.
    TEST_ASSERT_TRUE(battery_guess_present(3970, 3982));
    // Batteria quasi scarica ma ancora in grado di tenerlo acceso.
    TEST_ASSERT_TRUE(battery_guess_present(3400, 3420));
    // Senza batteria: impulsi del caricabatterie, valori sparsi o troppo bassi.
    TEST_ASSERT_FALSE(battery_guess_present(3000, 4180));
    TEST_ASSERT_FALSE(battery_guess_present(2906, 2906));
}

void test_battery_charge_state(void) {
    // a batteria: si scarica
    TEST_ASSERT_EQUAL_INT(CHARGE_DISCHARGING, battery_charge_state(POWER_SRC_BATTERY, true, 3900));
    // USB senza batteria: niente da caricare
    TEST_ASSERT_EQUAL_INT(CHARGE_NONE, battery_charge_state(POWER_SRC_USB, false, 4140));
    // USB con batteria: in carica (letto sul device il 2026-10-11)...
    TEST_ASSERT_EQUAL_INT(CHARGE_CHARGING, battery_charge_state(POWER_SRC_USB, true, 4024));
    // ...finche' non arriva alla tensione di fine carica
    TEST_ASSERT_EQUAL_INT(CHARGE_FULL, battery_charge_state(POWER_SRC_USB, true, 4190));
}
