#pragma once
#include <stdbool.h>
#include <stdint.h>

// Modalita' di boot (spec tasti §3), decisa dalla causa di wake:
//  - BOOT_TIMER_SYNC: wake dal timer -> solo sync, senza tasti
//  - BOOT_CAPTURE:    click di PWR (o accensione con PWR premuto) -> registra subito;
//                     un click in piu' nei primi 2 s porta in DEV (vedi app_main)
//  - BOOT_SYNC_ONLY:  click di USER, oppure reset/accensione senza tasti -> solo sync
//  - BOOT_DEV:        non deciso al boot: ci si arriva dal triplo click durante CAPTURE
typedef enum { BOOT_SYNC_ONLY, BOOT_CAPTURE, BOOT_DEV, BOOT_TIMER_SYNC } boot_mode_t;

void power_init(void);
bool power_pwr_pressed(void);   // BOARD_BTN_PWR (GPIO18), wake source
bool power_user_pressed(void);  // BOARD_BTN_USER (GPIO0, strapping: sveglia solo dal deep sleep)
boot_mode_t power_boot_mode(void);
const char *power_boot_mode_name(boot_mode_t m);

// Scadenza di ciclo: dopo ms millisecondi forza power_deep_sleep() (spec §4.1).
// Chiamarla di nuovo riavvia il conteggio; cancel la disattiva (DEV mode).
void power_cycle_deadline_start(uint32_t ms);
void power_cycle_deadline_cancel(void);

// Deep sleep con wake EXT1 su PWR e USER e, se timer_ms > 0, anche dal timer. Non ritorna.
void power_deep_sleep(uint32_t timer_ms);
