#include "unity.h"
#include "screen.h"
#include <string.h>

void test_screen_battery_fill(void) {
    TEST_ASSERT_EQUAL_INT(0, screen_battery_fill_px(0, 28));
    TEST_ASSERT_EQUAL_INT(28, screen_battery_fill_px(100, 28));
    TEST_ASSERT_EQUAL_INT(14, screen_battery_fill_px(50, 28));
    TEST_ASSERT_EQUAL_INT(2, screen_battery_fill_px(8, 28));     // 8 %: si vede ancora qualcosa
    TEST_ASSERT_EQUAL_INT(0, screen_battery_fill_px(-1, 28));    // fuori scala: limitato
    TEST_ASSERT_EQUAL_INT(28, screen_battery_fill_px(140, 28));
}

void test_screen_status_draws_outcome_band(void) {
    static uint8_t fb[SCREEN_FB_BYTES];
    const screen_status_t st = { "15:45", SCREEN_BAT_DISCHARGING, 77, "Casa", 0, "sync ok" };
    screen_render_status(fb, &st);
    // l'ultima riga del pannello e' la fascia nera dell'esito
    for (int i = 0; i < SCREEN_W / 8; i++) TEST_ASSERT_EQUAL_HEX8(0xFF, fb[(SCREEN_H - 1) * (SCREEN_W / 8) + i]);
    // la prima riga e' bianca (barra in alto, sopra ora e batteria)
    for (int i = 0; i < SCREEN_W / 8; i++) TEST_ASSERT_EQUAL_HEX8(0x00, fb[i]);
    // il testo lungo non esce dal pannello: niente crash e larghezza misurabile
    TEST_ASSERT_TRUE(screen_text_width(&font_grotesk_20, "iPhone di Francesco") > SCREEN_W - 16);
}
