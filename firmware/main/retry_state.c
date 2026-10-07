#include "retry_state.h"
#include "esp_attr.h"

RTC_DATA_ATTR static unsigned         s_attempt = 0;
RTC_DATA_ATTR static status_outcome_t s_shown = STATUS_OUTCOME_NONE;

unsigned         retry_state_attempt(void) { return s_attempt; }
void             retry_state_bump(void) { if (s_attempt < 1000) s_attempt++; }
void             retry_state_reset(void) { s_attempt = 0; }
status_outcome_t retry_state_shown(void) { return s_shown; }
void             retry_state_set_shown(status_outcome_t outcome) { s_shown = outcome; }
