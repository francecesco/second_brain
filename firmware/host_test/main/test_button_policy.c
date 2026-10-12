#include "unity.h"
#include "button_policy.h"
#include "config.h"

// Fa avanzare il rilevatore con n campioni uguali; ritorna quante pressioni ha confermato.
static int feed(click_det_t *d, bool pressed, int n) {
    int clicks = 0;
    for (int i = 0; i < n; i++) clicks += click_det_step(d, pressed) ? 1 : 0;
    return clicks;
}

void test_click_det_counts_debounced_presses(void) {
    click_det_t d;
    click_det_init(&d, false);
    TEST_ASSERT_EQUAL_INT(0, feed(&d, false, 20));
    TEST_ASSERT_EQUAL_INT(1, feed(&d, true, 10));    // pressione: contata una volta sola
    TEST_ASSERT_EQUAL_INT(0, feed(&d, false, 10));
    TEST_ASSERT_EQUAL_INT(1, feed(&d, true, 5));     // seconda pressione
}

void test_click_det_ignores_bounce(void) {
    click_det_t d;
    click_det_init(&d, false);
    // un campione premuto isolato e' rimbalzo, non un click
    TEST_ASSERT_EQUAL_INT(0, feed(&d, true, SB_BUTTON_DEBOUNCE_SAMPLES - 1));
    TEST_ASSERT_EQUAL_INT(0, feed(&d, false, 10));
    // durante la pressione, un rilascio di un campione non fa un secondo click
    TEST_ASSERT_EQUAL_INT(1, feed(&d, true, 10));
    TEST_ASSERT_EQUAL_INT(0, feed(&d, false, 1));
    TEST_ASSERT_EQUAL_INT(0, feed(&d, true, 10));
}

void test_click_det_wake_press_not_counted(void) {
    // Il tasto e' ancora giu' quando parte il campionamento: e' il click che ha svegliato.
    click_det_t d;
    click_det_init(&d, true);
    TEST_ASSERT_EQUAL_INT(0, feed(&d, true, 30));
    TEST_ASSERT_EQUAL_INT(0, feed(&d, false, 10));
    TEST_ASSERT_EQUAL_INT(1, feed(&d, true, 10));    // il click successivo si'
}

void test_capture_gesture(void) {
    // click in piu' nella finestra iniziale: DEV (il secondo di un triplo click puo' cadere nell'avvio)
    TEST_ASSERT_EQUAL_INT(GESTURE_DEV, button_capture_gesture(500));
    TEST_ASSERT_EQUAL_INT(GESTURE_DEV, button_capture_gesture(SB_DEV_CLICK_WINDOW_MS - 1));
    // dopo la finestra: stop
    TEST_ASSERT_EQUAL_INT(GESTURE_STOP, button_capture_gesture(SB_DEV_CLICK_WINDOW_MS));
    TEST_ASSERT_EQUAL_INT(GESTURE_STOP, button_capture_gesture(60000));
}
