#pragma once
#include <stdint.h>
#include "esp_err.h"

// Osservatore del tasto PWR durante una registrazione (spec tasti §5): un task campiona il
// tasto ogni SB_BUTTON_SAMPLE_MS e annota l'istante di ogni click (ms dall'avvio). Corre da
// solo perche' il main resta bloccato per secondi nel refresh dell'e-Paper. Il click che ha
// svegliato il device, se il tasto e' ancora giu' all'avvio, non conta.
esp_err_t buttons_watch_start(void);
int       buttons_clicks(void);            // click osservati finora
uint32_t  buttons_click_ms(int i);         // istante dell'i-esimo (0 = primo)
