#pragma once
#include <stdbool.h>

// Deep sleep arriva nel Task 8: qui solo tasti e rilevamento boot mode.
typedef enum { BOOT_NORMAL, BOOT_DEV } boot_mode_t;

void power_init(void);
bool power_button_pressed(void);
boot_mode_t power_boot_mode(void);
