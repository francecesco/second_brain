#pragma once
#include <stdbool.h>
#include <stdint.h>

// Modalita' di boot (spec Fase 1a §4.2), decisa campionando i tasti nel primo secondo:
//  - BOOT_DEV:       USER premuto entro 1 s -> Wi-Fi + server push/status, resta sveglio
//  - BOOT_CAPTURE:   PWR tenuto per tutti i 300 ms iniziali -> registra subito
//  - BOOT_SYNC_ONLY: pressione breve di PWR, oppure reset/power-on -> solo sync
typedef enum { BOOT_SYNC_ONLY, BOOT_CAPTURE, BOOT_DEV } boot_mode_t;

void power_init(void);
bool power_pwr_pressed(void);   // BOARD_BTN_PWR (GPIO18), wake source
bool power_user_pressed(void);  // BOARD_BTN_USER (GPIO0, strapping: mai come wake)
boot_mode_t power_boot_mode(void);
const char *power_boot_mode_name(boot_mode_t m);

// Scadenza di ciclo: dopo ms millisecondi forza power_deep_sleep() (spec §4.1).
// Chiamarla di nuovo riavvia il conteggio; cancel la disattiva (DEV mode).
void power_cycle_deadline_start(uint32_t ms);
void power_cycle_deadline_cancel(void);

// Deep sleep con wake EXT1 su BOARD_BTN_PWR. Non ritorna.
void power_deep_sleep(void);
