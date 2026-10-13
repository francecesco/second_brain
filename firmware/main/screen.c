#include "screen.h"
#include "config.h"
#include <stdio.h>
#include <string.h>

// Geometria della schermata di stato (pixel).
#define MARGIN        8
#define BAR_H         30     // barra in alto: ora e batteria
#define BAT_W         36     // corpo dell'icona, bordo compreso
#define BAT_H         20
#define BAT_TIP_W     3
#define BAT_TIP_H     10
#define BAT_BORDER    2
#define FOOT_H        34     // piede della schermata Oggi: coda, rete, esito

static void px(uint8_t *fb, int x, int y, bool black)
{
    if (x < 0 || x >= SCREEN_W || y < 0 || y >= SCREEN_H) return;
    uint8_t mask = 0x80 >> (x % 8);
    uint8_t *b = &fb[y * (SCREEN_W / 8) + x / 8];
    if (black) *b |= mask; else *b &= (uint8_t)~mask;
}

static void rect(uint8_t *fb, int x, int y, int w, int h, bool black)
{
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++) px(fb, i, j, black);
}

static const font_glyph_t *glyph(const font_t *f, char c)
{
    if (c < f->first || c > f->last) c = '?';
    return &f->glyphs[c - f->first];
}

int screen_text_width(const font_t *f, const char *s)
{
    int w = 0;
    for (; s && *s; s++) w += glyph(f, *s)->width;
    return w;
}

static int draw_glyph(uint8_t *fb, const font_t *f, int x, int y, char c, bool black)
{
    const font_glyph_t *g = glyph(f, c);
    int stride = (g->width + 7) / 8;
    const uint8_t *rows = &f->bitmap[g->offset];
    for (int r = 0; r < f->height; r++)
        for (int col = 0; col < g->width; col++)
            if (rows[r * stride + col / 8] & (0x80 >> (col % 8))) px(fb, x + col, y + r, black);
    return g->width;
}

// Disegna s a partire da (x, y); se supera max_w lo tronca con "..". Ritorna la larghezza.
static int draw_text(uint8_t *fb, const font_t *f, int x, int y, const char *s, int max_w, bool black)
{
    if (!s) return 0;
    int full = screen_text_width(f, s);
    int dots = screen_text_width(f, "..");
    int limit = full <= max_w ? max_w : max_w - dots;
    int x0 = x;
    for (; *s; s++) {
        int w = glyph(f, *s)->width;
        if (x - x0 + w > limit) break;
        x += draw_glyph(fb, f, x, y, *s, black);
    }
    if (full > max_w) {
        x += draw_glyph(fb, f, x, y, '.', black);
        x += draw_glyph(fb, f, x, y, '.', black);
    }
    return x - x0;
}

int screen_battery_fill_px(int pct, int inner)
{
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    return (pct * inner + 50) / 100;
}

// Fulmine 9x14, disegnato in negativo sopra il riempimento dell'icona.
#define BOLT_W 9
#define BOLT_H 14
static const char *const BOLT[BOLT_H] = {
    ".....###.",
    "....###..",
    "...###...",
    "..###....",
    ".###.....",
    "#########",
    "#########",
    ".....###.",
    "....###..",
    "...###...",
    "..###....",
    ".###.....",
    ".##......",
    "#........",
};

static void draw_battery(uint8_t *fb, const screen_status_t *st)
{
    const font_t *f = &font_grotesk_16;
    int bx = SCREEN_W - MARGIN - BAT_TIP_W - BAT_W;
    int by = (BAR_H - BAT_H) / 2;
    int ty = (BAR_H - f->height) / 2;

    if (st->battery == SCREEN_BAT_NONE) {
        int w = screen_text_width(f, "USB");
        draw_text(fb, f, SCREEN_W - MARGIN - w, ty, "USB", w, true);
        return;
    }
    // corpo e polo
    rect(fb, bx, by, BAT_W, BAT_H, true);
    rect(fb, bx + BAT_BORDER, by + BAT_BORDER, BAT_W - 2 * BAT_BORDER, BAT_H - 2 * BAT_BORDER, false);
    rect(fb, bx + BAT_W, by + (BAT_H - BAT_TIP_H) / 2, BAT_TIP_W, BAT_TIP_H, true);

    int inner_w = BAT_W - 2 * (BAT_BORDER + 1);
    int inner_h = BAT_H - 2 * (BAT_BORDER + 1);
    int ix = bx + BAT_BORDER + 1, iy = by + BAT_BORDER + 1;
    int pct = st->battery == SCREEN_BAT_FULL ? 100 : st->pct;
    if (st->battery != SCREEN_BAT_UNKNOWN) rect(fb, ix, iy, screen_battery_fill_px(pct, inner_w), inner_h, true);

    if (st->battery == SCREEN_BAT_CHARGING) {
        int ox = bx + (BAT_W - BOLT_W) / 2, oy = by + (BAT_H - BOLT_H) / 2;
        for (int r = 0; r < BOLT_H; r++)
            for (int c = 0; c < BOLT_W; c++) {
                if (BOLT[r][c] != '#') continue;
                // bianco sulla parte piena, nero su quella vuota: si legge con ogni carica
                int x = ox + c, y = oy + r;
                bool on_fill = x < ix + screen_battery_fill_px(pct, inner_w);
                px(fb, x, y, !on_fill);
            }
    }

    // percentuale a sinistra dell'icona
    char txt[8];
    if (st->battery == SCREEN_BAT_UNKNOWN || st->pct < 0) snprintf(txt, sizeof(txt), "?");
    else snprintf(txt, sizeof(txt), "%d%%", pct);
    int w = screen_text_width(f, txt);
    draw_text(fb, f, bx - 4 - w, ty, txt, w, true);
}

// Barra in alto: ora a sinistra, batteria a destra.
static void draw_bar(uint8_t *fb, const screen_status_t *st)
{
    draw_text(fb, &font_grotesk_20, MARGIN, (BAR_H - font_grotesk_20.height) / 2 + 1,
              st->clock ? st->clock : "--:--", 90, true);
    draw_battery(fb, st);
}

void screen_render_status(uint8_t *fb, const screen_status_t *st)
{
    memset(fb, 0, SCREEN_FB_BYTES);

    draw_bar(fb, st);
    rect(fb, 0, BAR_H, SCREEN_W, 2, true);

    // rete e coda
    const font_t *f = &font_grotesk_20;
    int y = BAR_H + 10;
    draw_text(fb, f, MARGIN, y, st->network ? st->network : "nessuna rete", SCREEN_W - 2 * MARGIN, true);
    char q[24];
    snprintf(q, sizeof(q), "coda: %d", st->queue);
    draw_text(fb, f, MARGIN, y + 32, q, SCREEN_W - 2 * MARGIN, true);

    // esito in grande, in negativo su fascia nera; se non ci sta, un corpo piu' piccolo
    const font_t *big = &font_grotesk_28;
    const char *out = st->outcome ? st->outcome : "";
    int band_y = SCREEN_H - big->height - 14;
    rect(fb, 0, band_y, SCREEN_W, SCREEN_H - band_y, true);
    const font_t *of = screen_text_width(big, out) <= SCREEN_W - 2 * MARGIN ? big : &font_grotesk_20;
    int oy = band_y + (SCREEN_H - band_y - of->height) / 2;
    draw_text(fb, of, MARGIN, oy, out, SCREEN_W - 2 * MARGIN, false);
}

void screen_render_today(uint8_t *fb, const screen_status_t *st, const uint8_t *digest)
{
    memset(fb, 0, SCREEN_FB_BYTES);
    draw_bar(fb, st);
    // l'immagine del backend ha lo stesso formato del framebuffer: copia di righe intere
    if (digest) memcpy(&fb[SB_DIGEST_Y * (SCREEN_W / 8)], digest, SB_DIGEST_BYTES);

    // piede: "esito | coda N | rete" (la rete, tagliata in fondo, e' la parte meno importante);
    // senza rete "no wifi | coda N": l'esito sarebbe solo "no sync", che ripeterebbe lo stesso;
    // se invece c'e' un esito di cattura ("Salvato 0:07", "SD piena", ...) quello vince
    int foot_y = SCREEN_H - FOOT_H;
    if (st->alert) rect(fb, 0, foot_y, SCREEN_W, FOOT_H, true);
    char line[96];
    if (st->network)
        snprintf(line, sizeof(line), "%s | coda %d | %s", st->outcome ? st->outcome : "", st->queue, st->network);
    else if (st->outcome && st->outcome[0] && strcmp(st->outcome, "no sync") != 0)
        snprintf(line, sizeof(line), "%s | coda %d", st->outcome, st->queue);
    else
        snprintf(line, sizeof(line), "no wifi | coda %d", st->queue);
    const font_t *f = &font_grotesk_16;
    draw_text(fb, f, MARGIN, foot_y + (FOOT_H - f->height) / 2, line, SCREEN_W - 2 * MARGIN, !st->alert);
}

void screen_render_lines(uint8_t *fb, const char *const lines[], int n)
{
    memset(fb, 0, SCREEN_FB_BYTES);
    const font_t *f = &font_grotesk_20;
    const int step = 32;
    int y = (SCREEN_H - (n - 1) * step - f->height) / 2;
    for (int i = 0; i < n; i++, y += step) draw_text(fb, f, MARGIN, y, lines[i] ? lines[i] : "", SCREEN_W - 2 * MARGIN, true);
}
