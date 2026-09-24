// e-Paper display driver (1.54", 200x200, Waveshare "ESP32-S3 1.54inch e-Paper
// Dev Board" B/W, hardware V2).
//
// REDO: la scheda reale monta un pannello B/N (pannello GDEH0154D67, controller
// SSD1681), NON il pannello 4 colori usato per errore nel tentativo precedente.
// Ported from the CORRECT Waveshare reference repo:
//   github.com/waveshareteam/ESP32-S3-ePaper-1.54 (clone di riferimento in /tmp,
//   non incluso nel repo)
//   02_Example/ESP-IDF/V2/12_RTC_Sleep_Test/components/epaper_driver_bsp/epaper_driver_bsp.{h,cpp}
//     -> init pannello SSD1681 (SWRESET 0x12, Driver Output Control 0x01, Data
//        Entry Mode 0x11, RAM X/Y window 0x44/0x45, RAM X/Y counter 0x4E/0x4F,
//        Border Waveform 0x3C, Temperature Sensor 0x18, Display Update Control
//        0x22 + Master Activation 0x20, LUT custom via 0x32/0x3F/0x03/0x04/0x2C),
//        BUSY polarity (read_busy(): "LOW: idle, HIGH: busy"), refresh (Write
//        RAM B/W 0x24 + Display Update Control 0x22=0xC7 + Master Activation
//        0x20). Il font (font12, Courier New 7x12) e la logica di rendering
//        testo di questo file non provengono da questo driver (che non ha un
//        livello testo) e sono stati mantenuti dal tentativo precedente.
//   02_Example/ESP-IDF/V2/12_RTC_Sleep_Test/main/user_config.h
//     -> pin map (vedi board.h)

#include "display.h"
#include "board.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"

static const char *TAG = "epd";

// Timeout BUSY: un refresh full-update SSD1681 può richiedere alcuni secondi
// (tipicamente 1-3s per un pannello 1.54" 200x200); teniamo comunque un
// margine ampio (25s) per non tagliare la traccia diagnostica in caso di
// comportamento anomalo sull'hardware reale.
#define EPD_BUSY_TIMEOUT_MS 25000
#define EPD_SPI_CLOCK_HZ (20 * 1000 * 1000)

static spi_device_handle_t s_spi;
static bool s_ready = false;

// Framebuffer 1 bit/pixel, 200x200 -> 5000 byte, statico (niente PSRAM su questa scheda).
// Contratto pubblico (invariato): bit=1 -> nero, bit=0 -> bianco (vedi app_main.c).
static uint8_t s_fb[(DISPLAY_W * DISPLAY_H) / 8];

// Buffer di appoggio per l'invio alla RAM del controller SSD1681: la RAM B/N
// del SSD1681 usa la convenzione opposta al nostro framebuffer pubblico
// (bit=1 -> bianco, bit=0 -> nero, come da EPD_Clear()/EPD_DrawColorPixel() del
// driver di riferimento: DRIVER_COLOR_WHITE=0xFF che imposta i bit, DRIVER_COLOR_BLACK=0x0
// che li azzera). display_blit_1bit inverte quindi ogni byte prima di scriverlo
// in RAM via comando 0x24.
static uint8_t s_panel_buf[(DISPLAY_W * DISPLAY_H) / 8];

// ---------------------------------------------------------------------------
// LUT full-refresh SSD1681 per pannello 1.54" (WF_Full_1IN54), 159 byte,
// copiata verbatim da epaper_driver_bsp.cpp (driver ufficiale di riferimento).
// Layout: [0..152] = LUT (0x32), [153] = VGH/VSH1/VSH2/VSL (0x3F),
// [154] = timing (0x03), [155..157] = timing (0x04, 3 byte), [158] = frame
// rate register (0x2C).
// ---------------------------------------------------------------------------
static const uint8_t s_lut_full_1in54[159] = {
    0x80, 0x48, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x40, 0x48, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x80, 0x48, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x40, 0x48, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x08, 0x01, 0x00, 0x08, 0x01, 0x00, 0x02,
    0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x00, 0x00, 0x00,
    0x22, 0x17, 0x41, 0x00, 0x32, 0x20,
};

// ---------------------------------------------------------------------------
// Font bitmap (Courier New 12pt, 7x12), invariato dal tentativo precedente.
// Glifi da ' ' (0x20) a '~' (0x7E), 1 byte/riga (larghezza 7 <= 8 bit), 12 righe/glifo.
// ---------------------------------------------------------------------------
#define FONT_W 7
#define FONT_H 12
#define FONT_FIRST_CHAR ' '
#define FONT_LAST_CHAR '~'

static const uint8_t s_font12_table[] = {
    // ' '
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '!'
    0x00, 0x10, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00,
    // '"'
    0x00, 0x6C, 0x48, 0x48, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '#'
    0x00, 0x14, 0x14, 0x28, 0x7C, 0x28, 0x7C, 0x28, 0x50, 0x50, 0x00, 0x00,
    // '$'
    0x00, 0x10, 0x38, 0x40, 0x40, 0x38, 0x48, 0x70, 0x10, 0x10, 0x00, 0x00,
    // '%'
    0x00, 0x20, 0x50, 0x20, 0x0C, 0x70, 0x08, 0x14, 0x08, 0x00, 0x00, 0x00,
    // '&'
    0x00, 0x00, 0x00, 0x18, 0x20, 0x20, 0x54, 0x48, 0x34, 0x00, 0x00, 0x00,
    // '\''
    0x00, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '('
    0x00, 0x08, 0x08, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x08, 0x08, 0x00,
    // ')'
    0x00, 0x20, 0x20, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x20, 0x20, 0x00,
    // '*'
    0x00, 0x10, 0x7C, 0x10, 0x28, 0x28, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '+'
    0x00, 0x00, 0x10, 0x10, 0x10, 0xFE, 0x10, 0x10, 0x10, 0x00, 0x00, 0x00,
    // ','
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x10, 0x30, 0x20, 0x00,
    // '-'
    0x00, 0x00, 0x00, 0x00, 0x00, 0x7C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '.'
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x30, 0x30, 0x00, 0x00, 0x00,
    // '/'
    0x00, 0x04, 0x04, 0x08, 0x08, 0x10, 0x10, 0x20, 0x20, 0x40, 0x00, 0x00,
    // '0'
    0x00, 0x38, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // '1'
    0x00, 0x30, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x7C, 0x00, 0x00, 0x00,
    // '2'
    0x00, 0x38, 0x44, 0x04, 0x08, 0x10, 0x20, 0x44, 0x7C, 0x00, 0x00, 0x00,
    // '3'
    0x00, 0x38, 0x44, 0x04, 0x18, 0x04, 0x04, 0x44, 0x38, 0x00, 0x00, 0x00,
    // '4'
    0x00, 0x0C, 0x14, 0x14, 0x24, 0x44, 0x7E, 0x04, 0x0E, 0x00, 0x00, 0x00,
    // '5'
    0x00, 0x3C, 0x20, 0x20, 0x38, 0x04, 0x04, 0x44, 0x38, 0x00, 0x00, 0x00,
    // '6'
    0x00, 0x1C, 0x20, 0x40, 0x78, 0x44, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // '7'
    0x00, 0x7C, 0x44, 0x04, 0x08, 0x08, 0x08, 0x10, 0x10, 0x00, 0x00, 0x00,
    // '8'
    0x00, 0x38, 0x44, 0x44, 0x38, 0x44, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // '9'
    0x00, 0x38, 0x44, 0x44, 0x44, 0x3C, 0x04, 0x08, 0x70, 0x00, 0x00, 0x00,
    // ':'
    0x00, 0x00, 0x00, 0x30, 0x30, 0x00, 0x00, 0x30, 0x30, 0x00, 0x00, 0x00,
    // ';'
    0x00, 0x00, 0x00, 0x18, 0x18, 0x00, 0x00, 0x18, 0x30, 0x20, 0x00, 0x00,
    // '<'
    0x00, 0x00, 0x0C, 0x10, 0x60, 0x80, 0x60, 0x10, 0x0C, 0x00, 0x00, 0x00,
    // '='
    0x00, 0x00, 0x00, 0x00, 0x7C, 0x00, 0x7C, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '>'
    0x00, 0x00, 0xC0, 0x20, 0x18, 0x04, 0x18, 0x20, 0xC0, 0x00, 0x00, 0x00,
    // '?'
    0x00, 0x00, 0x18, 0x24, 0x04, 0x08, 0x10, 0x00, 0x30, 0x00, 0x00, 0x00,
    // '@'
    0x38, 0x44, 0x44, 0x4C, 0x54, 0x54, 0x4C, 0x40, 0x44, 0x38, 0x00, 0x00,
    // 'A'
    0x00, 0x30, 0x10, 0x28, 0x28, 0x28, 0x7C, 0x44, 0xEE, 0x00, 0x00, 0x00,
    // 'B'
    0x00, 0xF8, 0x44, 0x44, 0x78, 0x44, 0x44, 0x44, 0xF8, 0x00, 0x00, 0x00,
    // 'C'
    0x00, 0x3C, 0x44, 0x40, 0x40, 0x40, 0x40, 0x44, 0x38, 0x00, 0x00, 0x00,
    // 'D'
    0x00, 0xF0, 0x48, 0x44, 0x44, 0x44, 0x44, 0x48, 0xF0, 0x00, 0x00, 0x00,
    // 'E'
    0x00, 0xFC, 0x44, 0x50, 0x70, 0x50, 0x40, 0x44, 0xFC, 0x00, 0x00, 0x00,
    // 'F'
    0x00, 0x7E, 0x22, 0x28, 0x38, 0x28, 0x20, 0x20, 0x70, 0x00, 0x00, 0x00,
    // 'G'
    0x00, 0x3C, 0x44, 0x40, 0x40, 0x4E, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // 'H'
    0x00, 0xEE, 0x44, 0x44, 0x7C, 0x44, 0x44, 0x44, 0xEE, 0x00, 0x00, 0x00,
    // 'I'
    0x00, 0x7C, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x7C, 0x00, 0x00, 0x00,
    // 'J'
    0x00, 0x3C, 0x08, 0x08, 0x08, 0x48, 0x48, 0x48, 0x30, 0x00, 0x00, 0x00,
    // 'K'
    0x00, 0xEE, 0x44, 0x48, 0x50, 0x70, 0x48, 0x44, 0xE6, 0x00, 0x00, 0x00,
    // 'L'
    0x00, 0x70, 0x20, 0x20, 0x20, 0x20, 0x24, 0x24, 0x7C, 0x00, 0x00, 0x00,
    // 'M'
    0x00, 0xEE, 0x6C, 0x6C, 0x54, 0x54, 0x44, 0x44, 0xEE, 0x00, 0x00, 0x00,
    // 'N'
    0x00, 0xEE, 0x64, 0x64, 0x54, 0x54, 0x54, 0x4C, 0xEC, 0x00, 0x00, 0x00,
    // 'O'
    0x00, 0x38, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // 'P'
    0x00, 0x78, 0x24, 0x24, 0x24, 0x38, 0x20, 0x20, 0x70, 0x00, 0x00, 0x00,
    // 'Q'
    0x00, 0x38, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x38, 0x1C, 0x00, 0x00,
    // 'R'
    0x00, 0xF8, 0x44, 0x44, 0x44, 0x78, 0x48, 0x44, 0xE2, 0x00, 0x00, 0x00,
    // 'S'
    0x00, 0x34, 0x4C, 0x40, 0x38, 0x04, 0x04, 0x64, 0x58, 0x00, 0x00, 0x00,
    // 'T'
    0x00, 0xFE, 0x92, 0x10, 0x10, 0x10, 0x10, 0x10, 0x38, 0x00, 0x00, 0x00,
    // 'U'
    0x00, 0xEE, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // 'V'
    0x00, 0xEE, 0x44, 0x44, 0x28, 0x28, 0x28, 0x10, 0x10, 0x00, 0x00, 0x00,
    // 'W'
    0x00, 0xEE, 0x44, 0x44, 0x54, 0x54, 0x54, 0x54, 0x28, 0x00, 0x00, 0x00,
    // 'X'
    0x00, 0xC6, 0x44, 0x28, 0x10, 0x10, 0x28, 0x44, 0xC6, 0x00, 0x00, 0x00,
    // 'Y'
    0x00, 0xEE, 0x44, 0x28, 0x28, 0x10, 0x10, 0x10, 0x38, 0x00, 0x00, 0x00,
    // 'Z'
    0x00, 0x7C, 0x44, 0x08, 0x10, 0x10, 0x20, 0x44, 0x7C, 0x00, 0x00, 0x00,
    // '['
    0x00, 0x38, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x38, 0x00,
    // '\\'
    0x00, 0x40, 0x20, 0x20, 0x20, 0x10, 0x10, 0x08, 0x08, 0x08, 0x00, 0x00,
    // ']'
    0x00, 0x38, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x38, 0x00,
    // '^'
    0x00, 0x10, 0x10, 0x28, 0x44, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // '_'
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFE,
    // '`'
    0x00, 0x10, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    // 'a'
    0x00, 0x00, 0x00, 0x38, 0x44, 0x3C, 0x44, 0x44, 0x3E, 0x00, 0x00, 0x00,
    // 'b'
    0x00, 0xC0, 0x40, 0x58, 0x64, 0x44, 0x44, 0x44, 0xF8, 0x00, 0x00, 0x00,
    // 'c'
    0x00, 0x00, 0x00, 0x3C, 0x44, 0x40, 0x40, 0x44, 0x38, 0x00, 0x00, 0x00,
    // 'd'
    0x00, 0x0C, 0x04, 0x34, 0x4C, 0x44, 0x44, 0x44, 0x3E, 0x00, 0x00, 0x00,
    // 'e'
    0x00, 0x00, 0x00, 0x38, 0x44, 0x7C, 0x40, 0x40, 0x3C, 0x00, 0x00, 0x00,
    // 'f'
    0x00, 0x1C, 0x20, 0x7C, 0x20, 0x20, 0x20, 0x20, 0x7C, 0x00, 0x00, 0x00,
    // 'g'
    0x00, 0x00, 0x00, 0x36, 0x4C, 0x44, 0x44, 0x44, 0x3C, 0x04, 0x38, 0x00,
    // 'h'
    0x00, 0xC0, 0x40, 0x58, 0x64, 0x44, 0x44, 0x44, 0xEE, 0x00, 0x00, 0x00,
    // 'i'
    0x00, 0x10, 0x00, 0x70, 0x10, 0x10, 0x10, 0x10, 0x7C, 0x00, 0x00, 0x00,
    // 'j'
    0x00, 0x10, 0x00, 0x78, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x70, 0x00,
    // 'k'
    0x00, 0xC0, 0x40, 0x5C, 0x48, 0x70, 0x50, 0x48, 0xDC, 0x00, 0x00, 0x00,
    // 'l'
    0x00, 0x30, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x7C, 0x00, 0x00, 0x00,
    // 'm'
    0x00, 0x00, 0x00, 0xE8, 0x54, 0x54, 0x54, 0x54, 0xFE, 0x00, 0x00, 0x00,
    // 'n'
    0x00, 0x00, 0x00, 0xD8, 0x64, 0x44, 0x44, 0x44, 0xEE, 0x00, 0x00, 0x00,
    // 'o'
    0x00, 0x00, 0x00, 0x38, 0x44, 0x44, 0x44, 0x44, 0x38, 0x00, 0x00, 0x00,
    // 'p'
    0x00, 0x00, 0x00, 0xD8, 0x64, 0x44, 0x44, 0x44, 0x78, 0x40, 0xE0, 0x00,
    // 'q'
    0x00, 0x00, 0x00, 0x36, 0x4C, 0x44, 0x44, 0x44, 0x3C, 0x04, 0x0E, 0x00,
    // 'r'
    0x00, 0x00, 0x00, 0x6C, 0x30, 0x20, 0x20, 0x20, 0x7C, 0x00, 0x00, 0x00,
    // 's'
    0x00, 0x00, 0x00, 0x3C, 0x44, 0x38, 0x04, 0x44, 0x78, 0x00, 0x00, 0x00,
    // 't'
    0x00, 0x00, 0x20, 0x7C, 0x20, 0x20, 0x20, 0x22, 0x1C, 0x00, 0x00, 0x00,
    // 'u'
    0x00, 0x00, 0x00, 0xCC, 0x44, 0x44, 0x44, 0x4C, 0x36, 0x00, 0x00, 0x00,
    // 'v'
    0x00, 0x00, 0x00, 0xEE, 0x44, 0x44, 0x28, 0x28, 0x10, 0x00, 0x00, 0x00,
    // 'w'
    0x00, 0x00, 0x00, 0xEE, 0x44, 0x54, 0x54, 0x54, 0x28, 0x00, 0x00, 0x00,
    // 'x'
    0x00, 0x00, 0x00, 0xCC, 0x48, 0x30, 0x30, 0x48, 0xCC, 0x00, 0x00, 0x00,
    // 'y'
    0x00, 0x00, 0x00, 0xEE, 0x44, 0x24, 0x28, 0x18, 0x10, 0x10, 0x78, 0x00,
    // 'z'
    0x00, 0x00, 0x00, 0x7C, 0x48, 0x10, 0x20, 0x44, 0x7C, 0x00, 0x00, 0x00,
    // '{'
    0x00, 0x08, 0x10, 0x10, 0x10, 0x10, 0x20, 0x10, 0x10, 0x10, 0x08, 0x00,
    // '|'
    0x00, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x00, 0x00,
    // '}'
    0x00, 0x20, 0x10, 0x10, 0x10, 0x10, 0x08, 0x10, 0x10, 0x10, 0x20, 0x00,
    // '~'
    0x00, 0x00, 0x00, 0x00, 0x00, 0x24, 0x58, 0x00, 0x00, 0x00, 0x00, 0x00,
};

// ---------------------------------------------------------------------------
// GPIO / SPI low level (CS/DC/RST manuali, come nel driver di riferimento:
// spics_io_num = -1, toggling manuale di CS/DC via gpio_set_level).
// ---------------------------------------------------------------------------

static inline void epd_rst(int level) { gpio_set_level(BOARD_EPD_RST, level); }
static inline void epd_dc(int level) { gpio_set_level(BOARD_EPD_DC, level); }
static inline void epd_cs(int level) { gpio_set_level(BOARD_EPD_CS, level); }

static esp_err_t epd_spi_send_byte(uint8_t byte)
{
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = 8;
    t.tx_buffer = &byte;
    return spi_device_polling_transmit(s_spi, &t);
}

static esp_err_t epd_send_command(uint8_t reg)
{
    epd_dc(0);
    epd_cs(0);
    esp_err_t err = epd_spi_send_byte(reg);
    epd_cs(1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPI error sending command 0x%02X: %s", reg, esp_err_to_name(err));
    }
    return err;
}

static esp_err_t epd_send_data(uint8_t data)
{
    epd_dc(1);
    epd_cs(0);
    esp_err_t err = epd_spi_send_byte(data);
    epd_cs(1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPI error sending data 0x%02X: %s", data, esp_err_to_name(err));
    }
    return err;
}

// Trasferimento bulk (usato per la LUT a 153 byte e per il framebuffer a 5000
// byte), porting di writeBytes() in epaper_driver_bsp.cpp: un'unica transazione
// SPI con DC=1/CS=0 per tutta la durata del blocco, invece di un byte per volta.
static esp_err_t epd_write_bytes(const uint8_t *data, size_t len)
{
    epd_dc(1);
    epd_cs(0);
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = 8 * len;
    t.tx_buffer = data;
    esp_err_t err = spi_device_polling_transmit(s_spi, &t);
    epd_cs(1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPI error writing %u bytes: %s", (unsigned)len, esp_err_to_name(err));
    }
    return err;
}

// ---------------------------------------------------------------------------
// BUSY handshake.
//
// Redo (SSD1681 B/W): polarità confermata dal driver ufficiale della scheda
// CORRETTA (epaper_driver_bsp.cpp, read_busy()):
//   "while(gpio_get_level(busy) == 1) vTaskDelay(...); // LOW: idle, HIGH: busy"
// cioè BUSY=HIGH(1) mentre il pannello è occupato, BUSY=LOW(0) quando è
// idle/pronto. Confermato indipendentemente dal crate Rust epd-waveshare
// (epd1in54_v2, stesso pannello GDEH0154D67): IS_BUSY_LOW = false.
// QUESTO È L'OPPOSTO della polarità "idle=HIGH" usata nel tentativo precedente
// (derivata dal driver del pannello 4 colori sbagliato).
//
// Strumentazione mantenuta invariata rispetto al tentativo precedente (livello
// raw all'avvio, log di ogni transizione con timestamp, tempo totale osservato
// in stato busy, timeout 25s, debounce a 3 campioni) per poter confermare sul
// prossimo capture seriale che il refresh reale tiene la linea busy per
// secondi (comportamento atteso di un vero full-refresh SSD1681), invece dei
// millisecondi che avrebbe prodotto una polarità invertita per errore.
#define EPD_BUSY_POLL_MS 100
#define EPD_BUSY_IDLE_CONFIRM_SAMPLES 3 // ~300ms di idle (LOW) stabile prima di dichiarare "rilasciato"

static esp_err_t epd_wait_busy(const char *phase)
{
    int level = gpio_get_level(BOARD_EPD_BUSY);
    int last_level = level;
    int idle_run = (level == 0) ? 1 : 0;
    uint32_t total_busy_ms = 0;
    TickType_t t_start = xTaskGetTickCount();
    TickType_t t_busy_since = (level == 1) ? t_start : 0;

    ESP_LOGI(TAG, "epd: %s - waiting BUSY (livello raw iniziale=%d; busy=HIGH/idle=LOW)", phase, level);

    for (;;) {
        uint32_t elapsed_ms = (uint32_t)((xTaskGetTickCount() - t_start) * portTICK_PERIOD_MS);

        if (level == 0 && idle_run >= EPD_BUSY_IDLE_CONFIRM_SAMPLES) {
            ESP_LOGI(TAG, "epd: %s - BUSY released a t=%ums (tempo totale osservato in stato busy: %ums)",
                     phase, (unsigned int)elapsed_ms, (unsigned int)total_busy_ms);
            return ESP_OK;
        }
        if (elapsed_ms > EPD_BUSY_TIMEOUT_MS) {
            ESP_LOGE(TAG, "epd: %s - BUSY timeout dopo %ums (livello raw=%d, tempo totale osservato in stato busy: %ums)",
                     phase, (unsigned int)elapsed_ms, level, (unsigned int)total_busy_ms);
            return ESP_ERR_TIMEOUT;
        }

        vTaskDelay(pdMS_TO_TICKS(EPD_BUSY_POLL_MS));
        level = gpio_get_level(BOARD_EPD_BUSY);
        elapsed_ms = (uint32_t)((xTaskGetTickCount() - t_start) * portTICK_PERIOD_MS);

        if (level != last_level) {
            ESP_LOGI(TAG, "epd: %s - BUSY transizione a t=%ums: livello %d -> %d",
                     phase, (unsigned int)elapsed_ms, last_level, level);
            if (level == 1) {
                t_busy_since = xTaskGetTickCount();
                idle_run = 0;
            } else {
                total_busy_ms += (uint32_t)((xTaskGetTickCount() - t_busy_since) * portTICK_PERIOD_MS);
                idle_run = 1;
            }
            last_level = level;
        } else if (level == 0) {
            idle_run++;
        }
    }
}

// Reset hardware: RST=1, 50ms; RST=0, 20ms; RST=1, 50ms — timing esatto del
// driver di riferimento (EPD_Init(), righe iniziali).
static void epd_reset(void)
{
    epd_rst(1);
    vTaskDelay(pdMS_TO_TICKS(50));
    epd_rst(0);
    vTaskDelay(pdMS_TO_TICKS(20));
    epd_rst(1);
    vTaskDelay(pdMS_TO_TICKS(50));
}

static esp_err_t epd_gpio_init(void)
{
    gpio_config_t out_conf = {
        .pin_bit_mask = (1ULL << BOARD_EPD_RST) | (1ULL << BOARD_EPD_DC) | (1ULL << BOARD_EPD_CS),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&out_conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: GPIO config (CS/DC/RST) failed: %s", esp_err_to_name(err));
        return err;
    }

    // Come nel driver di riferimento (spi_gpio_init in epaper_driver_bsp.cpp):
    // la struct gpio_config_t viene riusata cambiando solo mode/pin_bit_mask,
    // quindi pull_up_en resta ENABLE anche per l'ingresso BUSY.
    gpio_config_t busy_conf = {
        .pin_bit_mask = (1ULL << BOARD_EPD_BUSY),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    err = gpio_config(&busy_conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: GPIO config (BUSY) failed: %s", esp_err_to_name(err));
        return err;
    }

    epd_cs(1);
    epd_rst(1);
    return ESP_OK;
}

static esp_err_t epd_spi_init(void)
{
    spi_bus_config_t buscfg = {
        .mosi_io_num = BOARD_EPD_MOSI,
        .miso_io_num = -1,
        .sclk_io_num = BOARD_EPD_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 65536,
    };
    esp_err_t err = spi_bus_initialize(BOARD_EPD_SPI_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: spi_bus_initialize failed: %s", esp_err_to_name(err));
        return err;
    }

    // CS pilotato manualmente (spics_io_num = -1), come nel driver di riferimento.
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = EPD_SPI_CLOCK_HZ,
        .mode = 0,
        .spics_io_num = -1,
        .queue_size = 1,
    };
    err = spi_bus_add_device(BOARD_EPD_SPI_HOST, &devcfg, &s_spi);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: spi_bus_add_device failed: %s", esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}

// Rollback di epd_spi_init(): rimuove il device e libera il bus SPI. Usata da
// display_init() quando un passo successivo (GPIO, init pannello) fallisce
// dopo che bus+device sono già stati creati, cosi' una successiva chiamata a
// display_init() puo' ripartire da zero invece di trovare il bus gia'
// inizializzato (spi_bus_initialize() fallirebbe con ESP_ERR_INVALID_STATE).
// Best-effort: logga eventuali errori ma non li propaga, per non mascherare
// l'errore originale che ha innescato il rollback.
static void epd_spi_deinit(void)
{
    esp_err_t err = spi_bus_remove_device(s_spi);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: spi_bus_remove_device failed durante rollback: %s", esp_err_to_name(err));
    }
    err = spi_bus_free(BOARD_EPD_SPI_HOST);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: spi_bus_free failed durante rollback: %s", esp_err_to_name(err));
    }
}

// Propaga il primo errore SPI incontrato invece di continuare a inviare byte
// dopo un guasto.
#define EPD_TRY(expr)                     \
    do {                                  \
        esp_err_t _epd_err = (expr);      \
        if (_epd_err != ESP_OK) {         \
            return _epd_err;              \
        }                                 \
    } while (0)

// SET_RAM_X/Y_ADDRESS_START_END_POSITION (0x44/0x45). Porting letterale di
// EPD_SetWindows() del driver di riferimento, inclusa la chiamata con
// argomenti (0, Width-1, Height-1, 0) usata da EPD_Init() qui sotto: dato che
// il pannello è quadrato (200x200) il risultato numerico coincide con la
// finestra RAM completa 0..199 su entrambi gli assi.
static esp_err_t epd_set_windows(uint16_t xstart, uint16_t ystart, uint16_t xend, uint16_t yend)
{
    EPD_TRY(epd_send_command(0x44));
    EPD_TRY(epd_send_data((xstart >> 3) & 0xFF));
    EPD_TRY(epd_send_data((xend >> 3) & 0xFF));

    EPD_TRY(epd_send_command(0x45));
    EPD_TRY(epd_send_data(ystart & 0xFF));
    EPD_TRY(epd_send_data((ystart >> 8) & 0xFF));
    EPD_TRY(epd_send_data(yend & 0xFF));
    EPD_TRY(epd_send_data((yend >> 8) & 0xFF));
    return ESP_OK;
}

// SET_RAM_X/Y_ADDRESS_COUNTER (0x4E/0x4F).
static esp_err_t epd_set_cursor(uint16_t xstart, uint16_t ystart)
{
    EPD_TRY(epd_send_command(0x4E));
    EPD_TRY(epd_send_data(xstart & 0xFF));

    EPD_TRY(epd_send_command(0x4F));
    EPD_TRY(epd_send_data(ystart & 0xFF));
    EPD_TRY(epd_send_data((ystart >> 8) & 0xFF));
    return ESP_OK;
}

// Carica la LUT custom (159 byte: 153 di LUT + 6 di voltage/timing/frame-rate),
// porting letterale di EPD_SetLut().
static esp_err_t epd_set_lut(const uint8_t *lut)
{
    EPD_TRY(epd_send_command(0x32));
    EPD_TRY(epd_write_bytes(lut, 153));
    EPD_TRY(epd_wait_busy("lut-load"));

    EPD_TRY(epd_send_command(0x3F));
    EPD_TRY(epd_send_data(lut[153]));

    EPD_TRY(epd_send_command(0x03));
    EPD_TRY(epd_send_data(lut[154]));

    EPD_TRY(epd_send_command(0x04));
    EPD_TRY(epd_send_data(lut[155]));
    EPD_TRY(epd_send_data(lut[156]));
    EPD_TRY(epd_send_data(lut[157]));

    EPD_TRY(epd_send_command(0x2C));
    EPD_TRY(epd_send_data(lut[158]));
    return ESP_OK;
}

// Sequenza di init pannello SSD1681, porting letterale di EPD_Init() (la parte
// successiva al reset hardware, che resta separata in epd_reset() sopra così
// display_init() può loggare le due fasi distintamente come nel tentativo
// precedente).
static esp_err_t epd_panel_init(void)
{
    EPD_TRY(epd_wait_busy("post-reset"));

    EPD_TRY(epd_send_command(0x12)); // SWRESET
    EPD_TRY(epd_wait_busy("swreset"));

    EPD_TRY(epd_send_command(0x01)); // Driver output control: MUX = 199 (altezza 200-1)
    EPD_TRY(epd_send_data(0xC7));
    EPD_TRY(epd_send_data(0x00));
    EPD_TRY(epd_send_data(0x01));

    EPD_TRY(epd_send_command(0x11)); // Data entry mode
    EPD_TRY(epd_send_data(0x01));

    EPD_TRY(epd_set_windows(0, DISPLAY_W - 1, DISPLAY_H - 1, 0));

    EPD_TRY(epd_send_command(0x3C)); // Border waveform
    EPD_TRY(epd_send_data(0x01));

    EPD_TRY(epd_send_command(0x18)); // Temperature sensor: interno
    EPD_TRY(epd_send_data(0x80));

    EPD_TRY(epd_send_command(0x22)); // Load temperature & waveform setting
    EPD_TRY(epd_send_data(0xB1));
    EPD_TRY(epd_send_command(0x20)); // Master activation

    EPD_TRY(epd_set_cursor(0, DISPLAY_H - 1));
    EPD_TRY(epd_wait_busy("init-load-waveform"));

    EPD_TRY(epd_set_lut(s_lut_full_1in54));
    return ESP_OK;
}

// Attivazione refresh full-update: Display Update Control (0x22=0xC7) +
// Master Activation (0x20) + attesa BUSY, porting letterale di EPD_TurnOnDisplay().
static esp_err_t epd_turn_on_display(void)
{
    EPD_TRY(epd_send_command(0x22));
    EPD_TRY(epd_send_data(0xC7));
    EPD_TRY(epd_send_command(0x20));
    return epd_wait_busy("refresh");
}

// ---------------------------------------------------------------------------
// Framebuffer 1bpp + font bitmap
// ---------------------------------------------------------------------------

static inline void fb_set_pixel(uint8_t *fb, int x, int y, int set)
{
    if (x < 0 || x >= DISPLAY_W || y < 0 || y >= DISPLAY_H) {
        return;
    }
    const int stride = DISPLAY_W / 8;
    const int idx = y * stride + (x / 8);
    const uint8_t mask = 0x80 >> (x % 8);
    if (set) {
        fb[idx] |= mask;
    } else {
        fb[idx] &= (uint8_t)~mask;
    }
}

static void draw_char(uint8_t *fb, int x0, int y0, char c)
{
    if (c < FONT_FIRST_CHAR || c > FONT_LAST_CHAR) {
        return; // caratteri non stampabili (es. '\n', '\0') vengono ignorati
    }
    const uint8_t *glyph = &s_font12_table[(size_t)(c - FONT_FIRST_CHAR) * FONT_H];
    for (int row = 0; row < FONT_H; row++) {
        uint8_t bits = glyph[row];
        for (int col = 0; col < FONT_W; col++) {
            int set = (bits & (0x80 >> col)) != 0;
            fb_set_pixel(fb, x0 + col, y0 + row, set);
        }
    }
}

static void draw_string(uint8_t *fb, int x0, int y0, const char *s)
{
    int x = x0;
    if (!s) {
        return;
    }
    for (; *s != '\0'; s++) {
        if (*s == '\n' || *s == '\r') {
            break;
        }
        if (x + FONT_W > DISPLAY_W) {
            break;
        }
        draw_char(fb, x, y0, *s);
        x += FONT_W + 1;
    }
}

// ---------------------------------------------------------------------------
// API pubblica
// ---------------------------------------------------------------------------

esp_err_t display_init(void)
{
    ESP_LOGI(TAG, "epd: power on (BOARD_EPD_PWR -> ON, attivo basso)");
    gpio_config_t pwr_conf = {
        .pin_bit_mask = (1ULL << BOARD_EPD_PWR),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&pwr_conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: PWR gpio_config failed: %s", esp_err_to_name(err));
        return err;
    }
    gpio_set_level(BOARD_EPD_PWR, 0); // 0 = ON
    // Tempo di assestamento del ramo di alimentazione prima di reset/init.
    vTaskDelay(pdMS_TO_TICKS(30));

    ESP_LOGI(TAG, "epd: SPI + GPIO init (host=%d)", (int)BOARD_EPD_SPI_HOST);
    err = epd_spi_init();
    if (err != ESP_OK) {
        return err;
    }
    // Da qui in poi, bus SPI + device sono acquisiti: qualunque fallimento
    // successivo deve rilasciarli (epd_spi_deinit()) prima di propagare
    // l'errore, altrimenti una successiva display_init() troverebbe il bus
    // già inizializzato e fallirebbe con ESP_ERR_INVALID_STATE invece di
    // poter ritentare da zero.
    err = epd_gpio_init();
    if (err != ESP_OK) {
        epd_spi_deinit();
        return err;
    }

    // Livello raw di BUSY prima di inviare qualsiasi comando SPI al pannello,
    // utile per capire lo stato di riposo "naturale" della linea. Con la
    // polarità SSD1681 corretta (busy=HIGH/idle=LOW) un valore stabile a 0 qui
    // indica linea a riposo/idle.
    ESP_LOGI(TAG, "epd: BUSY livello raw all'avvio (prima di reset/init) = %d",
             gpio_get_level(BOARD_EPD_BUSY));

    ESP_LOGI(TAG, "epd: reset");
    epd_reset();

    ESP_LOGI(TAG, "epd: panel init sequence (SSD1681)");
    err = epd_panel_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: panel init failed: %s", esp_err_to_name(err));
        epd_spi_deinit();
        return err;
    }
    ESP_LOGI(TAG, "epd: panel init done");

    s_ready = true;
    return ESP_OK;
}

void display_blit_1bit(const uint8_t *buf, int w, int h)
{
    if (!s_ready) {
        ESP_LOGE(TAG, "display_blit_1bit: display non inizializzato");
        return;
    }
    if (buf == NULL || w != DISPLAY_W || h != DISPLAY_H) {
        ESP_LOGE(TAG, "display_blit_1bit: parametri non validi (%dx%d, atteso %dx%d)",
                 w, h, DISPLAY_W, DISPLAY_H);
        return;
    }

    // Contratto pubblico display_blit_1bit: bit=1 -> nero, bit=0 -> bianco.
    // RAM B/N del SSD1681 (nessuna inversione via 0x21, non usata da questo
    // driver): bit=1 -> bianco, bit=0 -> nero (vedi EPD_Clear()/EPD_DrawColorPixel()
    // del driver di riferimento). Si inverte quindi ogni byte prima di scriverlo.
    for (size_t i = 0; i < sizeof(s_panel_buf); i++) {
        s_panel_buf[i] = (uint8_t)~buf[i];
    }

    ESP_LOGI(TAG, "epd: full refresh - invio framebuffer %dx%d (5000 byte 1bpp -> RAM 0x24)", w, h);
    esp_err_t err = epd_send_command(0x24); // WRITE_RAM (Black/White)
    if (err == ESP_OK) {
        err = epd_write_bytes(s_panel_buf, sizeof(s_panel_buf));
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: full refresh aborted, SPI error while sending pixel data: %s",
                 esp_err_to_name(err));
        return;
    }

    err = epd_turn_on_display();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "epd: full refresh done");
    } else {
        ESP_LOGE(TAG, "epd: full refresh failed: %s", esp_err_to_name(err));
    }
}

void display_text(const char *line1, const char *line2)
{
    if (!s_ready) {
        ESP_LOGE(TAG, "display_text: display non inizializzato");
        return;
    }
    memset(s_fb, 0x00, sizeof(s_fb)); // 0 = bianco (sfondo)
    draw_string(s_fb, 8, 70, line1);
    draw_string(s_fb, 8, 92, line2);
    display_blit_1bit(s_fb, DISPLAY_W, DISPLAY_H);
}

void display_lines_n(const char *const lines[], int n)
{
    if (!s_ready) return;
    display_wait_idle();
    if (n > DISPLAY_MAX_LINES) n = DISPLAY_MAX_LINES;
    memset(s_fb, 0x00, sizeof(s_fb));
    // Blocco di testo centrato verticalmente: passo 28 px (font 12 px + aria).
    const int step = 28;
    int y = (DISPLAY_H - (n - 1) * step - FONT_H) / 2;
    for (int i = 0; i < n; i++, y += step) {
        draw_string(s_fb, 8, y, lines[i] ? lines[i] : "");
    }
    display_blit_1bit(s_fb, DISPLAY_W, DISPLAY_H);
}

void display_lines(const char *l1, const char *l2, const char *l3)
{
    const char *const lines[3] = { l1, l2, l3 };
    display_lines_n(lines, 3);
}

// --- refresh asincrono ---------------------------------------------------------
#include "freertos/semphr.h"
static SemaphoreHandle_t s_idle = NULL;
static char s_async_l1[32], s_async_l2[32];

static void display_async_task(void *arg)
{
    (void)arg;
    display_text(s_async_l1, s_async_l2);
    xSemaphoreGive(s_idle);
    vTaskDelete(NULL);
}

void display_wait_idle(void)
{
    if (!s_idle) return;
    xSemaphoreTake(s_idle, pdMS_TO_TICKS(6000));
    xSemaphoreGive(s_idle);
}

void display_text_async(const char *line1, const char *line2)
{
    if (!s_idle) { s_idle = xSemaphoreCreateBinary(); if (s_idle) xSemaphoreGive(s_idle); }
    if (!s_idle || xSemaphoreTake(s_idle, pdMS_TO_TICKS(6000)) != pdTRUE) { display_text(line1, line2); return; }
    strlcpy(s_async_l1, line1 ? line1 : "", sizeof(s_async_l1));
    strlcpy(s_async_l2, line2 ? line2 : "", sizeof(s_async_l2));
    if (xTaskCreate(display_async_task, "epd_async", 4096, NULL, tskIDLE_PRIORITY + 2, NULL) != pdPASS) {
        display_text(s_async_l1, s_async_l2);
        xSemaphoreGive(s_idle);
    }
}
