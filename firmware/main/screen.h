#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "font_grotesk.h"

// Disegno delle schermate nel framebuffer 1 bit 200x200 (bit=1 nero), senza dipendenze da
// ESP-IDF: si compila anche sul Mac per l'anteprima (tools/screen_preview.c).
#define SCREEN_W 200
#define SCREEN_H 200
#define SCREEN_FB_BYTES (SCREEN_W * SCREEN_H / 8)

// Batteria come la mostra l'icona in alto a destra.
typedef enum {
    SCREEN_BAT_UNKNOWN,      // lettura fallita: icona vuota con "?"
    SCREEN_BAT_NONE,         // USB senza batteria: scritta "USB"
    SCREEN_BAT_DISCHARGING,  // a batteria
    SCREEN_BAT_CHARGING,     // USB, in carica: fulmine nell'icona
    SCREEN_BAT_FULL,         // USB, carica completa
} screen_battery_t;

typedef struct {
    const char      *clock;      // "15:45" o "--:--"
    screen_battery_t battery;
    int              pct;        // 0-100, -1 se non nota
    const char      *network;    // nome della rete, NULL senza rete
    int              queue;      // file in coda
    const char      *outcome;    // esito del ciclo, in grande
    bool             alert;      // schermata Oggi: piede in negativo
} screen_status_t;

// Schermata di stato: ora e batteria in alto, rete e coda al centro, esito in basso.
void screen_render_status(uint8_t *fb, const screen_status_t *st);

// Schermata "Oggi": intestazione come screen_render_status (ora e batteria), immagine del
// backend (SB_DIGEST_W x SB_DIGEST_H, formato del framebuffer) da y = SB_DIGEST_Y, piede su
// una riga "coda N | rete | esito" (rete "no wifi" se NULL), in negativo se alert.
void screen_render_today(uint8_t *fb, const screen_status_t *st, const uint8_t *digest);

// Righe di testo centrate verticalmente (DEV MODE, REC, messaggi brevi).
void screen_render_lines(uint8_t *fb, const char *const lines[], int n);

// Pixel dell'icona batteria pieni su inner pixel interni (pct fuori 0-100 viene limitato).
int screen_battery_fill_px(int pct, int inner);

// Larghezza in pixel di s con font f.
int screen_text_width(const font_t *f, const char *s);
