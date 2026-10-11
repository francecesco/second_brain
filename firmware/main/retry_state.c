#include "retry_state.h"
#include "esp_attr.h"

RTC_DATA_ATTR static status_view_t s_shown;   // azzerata: outcome NONE, mai mostrato

const status_view_t *retry_state_shown(void) { return &s_shown; }
void                 retry_state_set_shown(const status_view_t *view) { s_shown = *view; }
