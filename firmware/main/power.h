#pragma once
#include <stdbool.h>

typedef enum { BOOT_NORMAL, BOOT_DEV } boot_mode_t;

void power_init(void);
bool power_button_pressed(void);
boot_mode_t power_boot_mode(void);

// Entra in deep sleep con risveglio su BOARD_BTN_PWR (EXT1, active-low).
// Non ritorna: il risveglio riparte da un boot nuovo (reset), non da qui.
void power_deep_sleep(void);
