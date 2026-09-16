#pragma once
#include "esp_err.h"
#include <stdint.h>

#define DISPLAY_W 200
#define DISPLAY_H 200

esp_err_t display_init(void);
void display_text(const char *line1, const char *line2);
void display_blit_1bit(const uint8_t *buf, int w, int h);
