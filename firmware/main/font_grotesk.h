#pragma once
// Generato da tools/gen_font.py: Space Grotesk Bold (OFL) in bitmap 1 bit. Non modificare a mano.
#include <stdint.h>

typedef struct {
    uint8_t  width;     // pixel, = avanzamento
    uint16_t offset;    // in bitmap: height righe da (width + 7) / 8 byte
} font_glyph_t;

typedef struct {
    uint8_t             height;
    char                first, last;
    const font_glyph_t *glyphs;
    const uint8_t      *bitmap;
} font_t;

extern const font_t font_grotesk_16;
extern const font_t font_grotesk_20;
extern const font_t font_grotesk_28;
