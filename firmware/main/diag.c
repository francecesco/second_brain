#include "diag.h"
#include <string.h>
#include "esp_attr.h"
#include "esp_timer.h"
#include "esp_system.h"

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

void diag_note_heap(void)
{
    uint32_t now = esp_get_free_heap_size();
    if (s_diag.free_heap_min == 0 || now < s_diag.free_heap_min) s_diag.free_heap_min = now;
}
