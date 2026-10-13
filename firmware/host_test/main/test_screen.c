#include "unity.h"
#include "screen.h"
#include <string.h>
#include "config.h"

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
    const screen_status_t st = { "15:45", SCREEN_BAT_DISCHARGING, 77, "Casa", 0, "sync ok", false };
    screen_render_status(fb, &st);
    // l'ultima riga del pannello e' la fascia nera dell'esito
    for (int i = 0; i < SCREEN_W / 8; i++) TEST_ASSERT_EQUAL_HEX8(0xFF, fb[(SCREEN_H - 1) * (SCREEN_W / 8) + i]);
    // la prima riga e' bianca (barra in alto, sopra ora e batteria)
    for (int i = 0; i < SCREEN_W / 8; i++) TEST_ASSERT_EQUAL_HEX8(0x00, fb[i]);
    // il testo lungo non esce dal pannello: niente crash e larghezza misurabile
    TEST_ASSERT_TRUE(screen_text_width(&font_grotesk_20, "iPhone di Francesco") > SCREEN_W - 16);
}

#define ROW_BYTES (SCREEN_W / 8)

void test_screen_today_digest_rows_copied(void) {
    static uint8_t fb[SCREEN_FB_BYTES];
    static uint8_t digest[SB_DIGEST_BYTES];
    const screen_status_t st = { "15:45", SCREEN_BAT_DISCHARGING, 77, "Casa", 0, "sync ok", false };
    memset(digest, 0xFF, sizeof(digest));
    screen_render_today(fb, &st, digest);
    for (int y = SB_DIGEST_Y; y < SB_DIGEST_Y + SB_DIGEST_H; y++)
        for (int i = 0; i < ROW_BYTES; i++) TEST_ASSERT_EQUAL_HEX8(0xFF, fb[y * ROW_BYTES + i]);
    for (int i = 0; i < ROW_BYTES; i++) TEST_ASSERT_EQUAL_HEX8(0xFF, fb[i]);   // riga 0 nera (intestazione)
    memset(digest, 0x00, sizeof(digest));
    screen_render_today(fb, &st, digest);
    for (int y = SB_DIGEST_Y; y < SB_DIGEST_Y + SB_DIGEST_H; y++)
        for (int i = 0; i < ROW_BYTES; i++) TEST_ASSERT_EQUAL_HEX8(0x00, fb[y * ROW_BYTES + i]);
}

void test_screen_today_bars_always_negative(void) {
    static uint8_t fb[SCREEN_FB_BYTES], al[SCREEN_FB_BYTES];
    static uint8_t digest[SB_DIGEST_BYTES];
    screen_status_t st = { "15:45", SCREEN_BAT_DISCHARGING, 77, NULL, 2, "no sync", false };
    memset(digest, 0x00, sizeof(digest));
    screen_render_today(fb, &st, digest);
    st.alert = true;
    screen_render_today(al, &st, digest);
    uint8_t *v[2] = { fb, al };
    for (int k = 0; k < 2; k++) {
        // prima riga dell'intestazione e ultima del piede: tutto nero, con e senza alert
        for (int i = 0; i < ROW_BYTES; i++) {
            TEST_ASSERT_EQUAL_HEX8(0xFF, v[k][i]);
            TEST_ASSERT_EQUAL_HEX8(0xFF, v[k][(SCREEN_H - 1) * ROW_BYTES + i]);
        }
        // testo bianco nell'intestazione e nel piede
        int white_h = 0, white_f = 0;
        for (int y = 0; y < 30; y++)
            for (int i = 0; i < ROW_BYTES; i++) white_h += v[k][y * ROW_BYTES + i] != 0xFF;
        for (int y = SCREEN_H - 34; y < SCREEN_H; y++)
            for (int i = 0; i < ROW_BYTES; i++) white_f += v[k][y * ROW_BYTES + i] != 0xFF;
        TEST_ASSERT_TRUE(white_h > 0);
        TEST_ASSERT_TRUE(white_f > 0);
    }
    // il prefisso "! " distingue il piede in allarme
    TEST_ASSERT_TRUE(memcmp(fb, al, SCREEN_FB_BYTES) != 0);
    // l'intestazione e' uguale (l'alert tocca solo il piede)
    TEST_ASSERT_EQUAL_MEMORY(fb, al, 30 * ROW_BYTES);
}

void test_screen_status_header_stays_positive(void) {
    static uint8_t fb[SCREEN_FB_BYTES];
    const screen_status_t st = { "15:45", SCREEN_BAT_CHARGING, 50, "Casa", 0, "sync ok", false };
    screen_render_status(fb, &st);
    // la schermata classica non e' toccata: riga 0 bianca, qualche pixel nero nella barra
    int black = 0;
    for (int y = 0; y < 30; y++)
        for (int i = 0; i < ROW_BYTES; i++) black += fb[y * ROW_BYTES + i] != 0x00;
    TEST_ASSERT_TRUE(black > 0);
    for (int i = 0; i < ROW_BYTES; i++) TEST_ASSERT_EQUAL_HEX8(0x00, fb[i]);
}

void test_screen_today_null_digest(void) {
    static uint8_t fb[SCREEN_FB_BYTES];
    const screen_status_t st = { "15:45", SCREEN_BAT_DISCHARGING, 77, "Casa", 0, "sync ok", false };
    memset(fb, 0xFF, sizeof(fb));
    screen_render_today(fb, &st, NULL);
    for (int y = SB_DIGEST_Y; y < SB_DIGEST_Y + SB_DIGEST_H; y++)
        for (int i = 0; i < ROW_BYTES; i++) TEST_ASSERT_EQUAL_HEX8(0x00, fb[y * ROW_BYTES + i]);
}

void test_screen_today_no_network_shows_capture_outcome(void) {
    static uint8_t a[SCREEN_FB_BYTES], b[SCREEN_FB_BYTES], c[SCREEN_FB_BYTES];
    screen_status_t st = { "15:45", SCREEN_BAT_DISCHARGING, 77, NULL, 1, "SD piena", false };
    screen_render_today(a, &st, NULL);
    st.outcome = "no sync";
    screen_render_today(b, &st, NULL);
    st.outcome = NULL;
    screen_render_today(c, &st, NULL);
    // l'esito della cattura cambia il piede; "no sync" resta "no wifi | coda N"
    TEST_ASSERT_TRUE(memcmp(a, b, SCREEN_FB_BYTES) != 0);
    TEST_ASSERT_EQUAL_MEMORY(b, c, SCREEN_FB_BYTES);
}
