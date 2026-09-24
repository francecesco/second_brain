#pragma once
#include "esp_err.h"
#include <stdint.h>

#define DISPLAY_W 200
#define DISPLAY_H 200

esp_err_t display_init(void);
void display_text(const char *line1, const char *line2);
void display_lines(const char *l1, const char *l2, const char *l3);  // 3 righe, max 24 caratteri ciascuna
#define DISPLAY_MAX_LINES 5
void display_lines_n(const char *const lines[], int n);              // fino a 5 righe, spaziatura automatica
void display_blit_1bit(const uint8_t *buf, int w, int h);
