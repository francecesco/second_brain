#include "diag.h"
#include <string.h>
#include "esp_attr.h"
#include "esp_timer.h"

RTC_DATA_ATTR static diag_t s_diag;

diag_t *diag_begin(const char *mode)
{
    uint32_t seq = s_diag.seq + 1;
    memset(&s_diag, 0, sizeof(s_diag));
    s_diag.seq = seq;
    strncpy(s_diag.mode, mode, sizeof(s_diag.mode) - 1);
    return &s_diag;
}

diag_t *diag_get(void) { return &s_diag; }

uint32_t diag_now_ms(void) { return (uint32_t)(esp_timer_get_time() / 1000); }
