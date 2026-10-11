#!/usr/bin/env python3
"""Genera main/font_grotesk.{c,h}: Space Grotesk Bold in bitmap 1 bit, per il display.

Il font e' quello della UI web (backend/secondbrain/web/static/space-grotesk.woff2, licenza
OFL in space-grotesk-OFL.txt accanto), al peso 700. Ogni glifo ASCII stampabile diventa una
bitmap alta quanto la riga, larga quanto l'avanzamento del carattere (font proporzionale).
Soglia al 50 %: niente grigi.

Uso (serve fonttools, brotli, pillow; non entrano nel firmware):
    python tools/gen_font.py
"""
import io
import pathlib

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[2]
WOFF2 = ROOT / "backend/secondbrain/web/static/space-grotesk.woff2"
OUT = ROOT / "firmware/main"
SIZES = (16, 20, 28)          # px dell'em: testo piccolo, righe di stato, esito
FIRST, LAST = 0x20, 0x7E


def load_font(px):
    tt = TTFont(str(WOFF2))
    tt.flavor = None
    buf = io.BytesIO()
    tt.save(buf)
    buf.seek(0)
    font = ImageFont.truetype(buf, px)
    font.set_variation_by_axes([700])
    return font


def render(px):
    font = load_font(px)
    ascent, descent = font.getmetrics()
    height = ascent + descent
    glyphs, data = [], []
    for code in range(FIRST, LAST + 1):
        ch = chr(code)
        adv = max(1, round(font.getlength(ch)))
        img = Image.new("L", (adv + 4, height), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        stride = (adv + 7) // 8
        offset = len(data)
        for y in range(height):
            row = [0] * stride
            for x in range(adv):
                if img.getpixel((x, y)) >= 128:
                    row[x // 8] |= 0x80 >> (x % 8)
            data.extend(row)
        glyphs.append((adv, offset))
    return height, glyphs, data


def main():
    h_lines = [
        "#pragma once",
        "// Generato da tools/gen_font.py: Space Grotesk Bold (OFL) in bitmap 1 bit. Non modificare a mano.",
        "#include <stdint.h>",
        "",
        "typedef struct {",
        "    uint8_t  width;     // pixel, = avanzamento",
        "    uint16_t offset;    // in bitmap: height righe da (width + 7) / 8 byte",
        "} font_glyph_t;",
        "",
        "typedef struct {",
        "    uint8_t             height;",
        "    char                first, last;",
        "    const font_glyph_t *glyphs;",
        "    const uint8_t      *bitmap;",
        "} font_t;",
        "",
    ]
    c_lines = ['#include "font_grotesk.h"', ""]
    for px in SIZES:
        height, glyphs, data = render(px)
        name = f"font_grotesk_{px}"
        h_lines.append(f"extern const font_t {name};")
        c_lines.append(f"static const uint8_t {name}_bitmap[{len(data)}] = {{")
        for i in range(0, len(data), 16):
            c_lines.append("    " + ", ".join(f"0x{b:02X}" for b in data[i:i + 16]) + ",")
        c_lines.append("};")
        c_lines.append(f"static const font_glyph_t {name}_glyphs[{len(glyphs)}] = {{")
        for code, (w, off) in zip(range(FIRST, LAST + 1), glyphs):
            c_lines.append(f"    {{ {w}, {off} }},  // {chr(code)!r}")
        c_lines.append("};")
        c_lines.append(f"const font_t {name} = {{ {height}, 0x{FIRST:02X}, 0x{LAST:02X}, "
                       f"{name}_glyphs, {name}_bitmap }};")
        c_lines.append("")
    (OUT / "font_grotesk.h").write_text("\n".join(h_lines) + "\n")
    (OUT / "font_grotesk.c").write_text("\n".join(c_lines))


if __name__ == "__main__":
    main()
