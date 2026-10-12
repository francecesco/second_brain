#pragma once
#include <stdbool.h>
#include <stdint.h>
// Tasti a click (spec tasti §4): riconoscimento delle pressioni e decisione del gesto durante
// una registrazione. Funzioni pure, testate su host.

// Rilevatore di pressioni su campioni presi ogni SB_BUTTON_SAMPLE_MS. Una pressione conta
// quando il tasto resta giu' per SB_BUTTON_DEBOUNCE_SAMPLES campioni dopo essere stato su
// per altrettanti: i rimbalzi non contano e una pressione lunga conta una volta.
typedef struct {
    bool down;      // stato confermato
    int  run;       // campioni consecutivi in disaccordo con lo stato confermato
} click_det_t;

// pressed_now: livello al primo campione. Se il tasto e' gia' giu' (il click che ha
// svegliato il device) quella pressione non viene contata.
void click_det_init(click_det_t *d, bool pressed_now);

// Un campione; true se conferma una nuova pressione.
bool click_det_step(click_det_t *d, bool pressed);

// Gesto di un click di PWR arrivato ms_from_start dopo l'avvio dell'app durante una
// registrazione: nei primi SB_DEV_CLICK_WINDOW_MS e' parte di un triplo click (DEV; il
// secondo click spesso cade durante l'avvio e non si vede), dopo e' lo stop.
typedef enum { GESTURE_STOP, GESTURE_DEV } capture_gesture_t;
capture_gesture_t button_capture_gesture(uint32_t ms_from_start);
