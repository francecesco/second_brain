#pragma once
#include <stdbool.h>
#include <stdint.h>

// Converte un BSSID testuale "aa:bb:cc:dd:ee:ff" (separatore ':' o '-',
// esadecimali maiuscoli o minuscoli) nei 6 byte di out. Ritorna false su
// formato non valido (NULL, numero di ottetti diverso da 6, caratteri
// non-hex, separatori mancanti). Funzione pura, testata su host.
bool wifi_parse_bssid(const char *text, uint8_t out[6]);
