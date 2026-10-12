#include "button_policy.h"
#include "config.h"

void click_det_init(click_det_t *d, bool pressed_now)
{
    d->down = pressed_now;
    d->run = 0;
}

bool click_det_step(click_det_t *d, bool pressed)
{
    if (pressed == d->down) { d->run = 0; return false; }
    if (++d->run < SB_BUTTON_DEBOUNCE_SAMPLES) return false;
    d->down = pressed;
    d->run = 0;
    return pressed;
}

capture_gesture_t button_capture_gesture(uint32_t ms_from_start)
{
    return ms_from_start < SB_DEV_CLICK_WINDOW_MS ? GESTURE_DEV : GESTURE_STOP;
}
