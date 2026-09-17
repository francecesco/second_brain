// e-Paper display driver (1.54", 200x200, Waveshare "1.54G" panel).
//
// Ported from the Waveshare reference repo (Task 2/3 source):
//   github.com/waveshareteam/ESP32-S3-ePaper-1.54G (clone di riferimento, non incluso nel repo)
//   Example/ESP-IDF_5.5.1/09_E_Paper_Test/components/epaper_port/epaper_port.c
//     -> SPI bring-up (SPI3_HOST, manual CS/DC/RST, BUSY polling) e sequenza di init pannello
//        (comandi 0x4D/0x00 PSR/0x06 BTST_P/0x50 CDI/0x61 TRES/0xE9/0x30 PLL/0x04 power-on,
//        0x10 data-start-transmission, 0x12 display-refresh).
//   Example/ESP-IDF_5.5.1/09_E_Paper_Test/components/epaper_src/Fonts/font12.cpp
//     -> tabella bitmap font (Courier New 12pt, 7x12, spazio..'~'), copiata verbatim.
//
// NOTA sul pannello: il modulo reale è un e-Paper 4 colori (nero/bianco/giallo/rosso,
// 2 bit/pixel, 4 pixel/byte) e non un pannello 1-bit puro. L'interfaccia richiesta da
// questo task (`display_blit_1bit`, buffer 200x200 1 bit/pixel) resta comunque valida:
// ogni pixel del buffer 1-bit viene mappato sui soli indici colore NERO(0x0)/BIANCO(0x1)
// del controller, che sono bit-compatibili con una resa bianco/nero. Segnalato come nota,
// non come problema di pin (i pin BOARD_EPD_* sono confermati dal repo di riferimento).

#include "display.h"
#include "board.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"

static const char *TAG = "epd";

// Indici colore del controller (solo bianco/nero usati da questo driver).
#define EPD_COLOR_BLACK 0x0
#define EPD_COLOR_WHITE 0x1

// Fix round 2: alzato da 15000 a 25000 per non tagliare la traccia diagnostica
// di un refresh reale del pannello 4 colori, che puo' durare 15-20s.
#define EPD_BUSY_TIMEOUT_MS 25000
#define EPD_SPI_CLOCK_HZ (20 * 1000 * 1000)

static spi_device_handle_t s_spi;
static bool s_ready = false;

// Framebuffer 1 bit/pixel, 200x200 -> 5000 byte, statico (niente PSRAM su questa scheda).
static uint8_t s_fb[(DISPLAY_W * DISPLAY_H) / 8];

// ---------------------------------------------------------------------------
// Font bitmap (Courier New 12pt, 7x12), portato verbatim da font12.cpp Waveshare.
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
// GPIO / SPI low level (CS/DC/RST manuali, come nel driver di riferimento)
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

// Finding B (fix round 1): epd_send_command/epd_send_data ora restituiscono
// esp_err_t, cosi' epd_panel_init()/display_blit_1bit() possono rilevare e
// propagare un errore SPI a livello di singolo byte, invece di scoprirlo solo
// (o non scoprirlo affatto) tramite un successivo timeout di BUSY.
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

// Fix round 4: ripristinata la polarita' BUSY di riferimento idle=HIGH(1) /
// busy=LOW(0), confermata dal driver Waveshare epaper_port.c
// (`epaper_readbusyh`: `if (ReadBusy) return;`, cioe' esce=rilasciato quando
// il livello e' 1). L'inversione del round 3 (busy=HIGH) faceva interpretare
// la linea BUSY, che a riposo sta stabilmente a 1 (idle), come "occupata per
// sempre": il wait di power-on/refresh andava in timeout a 25s e
// ESP_ERROR_CHECK(display_init()) abortiva -> BOOT LOOP. Con la polarita'
// corretta la linea a 1 viene letta come idle e il handshake non blocca piu'
// il boot.
//
// La strumentazione del round 2 (livello raw iniziale, log di ogni
// transizione con timestamp, tempo totale osservato in stato busy, timeout
// 25s, debounce a 3 campioni) e' mantenuta invariata: serve a distinguere sul
// prossimo capture seriale "il pannello entra davvero in busy per secondi"
// (fix riuscito) da "BUSY non scende mai, refresh mai eseguito".
//
// Polarita' implementata: BUSY==1 (HIGH) = idle, BUSY==0 (LOW) = occupato;
// rilascio dichiarato dopo EPD_BUSY_IDLE_CONFIRM_SAMPLES campioni HIGH stabili.
#define EPD_BUSY_POLL_MS 100
#define EPD_BUSY_IDLE_CONFIRM_SAMPLES 3 // ~300ms di idle (HIGH) stabile prima di dichiarare "rilasciato"

static esp_err_t epd_wait_busy(const char *phase)
{
    int level = gpio_get_level(BOARD_EPD_BUSY);
    int last_level = level;
    int idle_run = (level == 1) ? 1 : 0;
    uint32_t total_busy_ms = 0;
    TickType_t t_start = xTaskGetTickCount();
    TickType_t t_busy_since = (level == 0) ? t_start : 0;

    ESP_LOGI(TAG, "epd: %s - waiting BUSY (livello raw iniziale=%d)", phase, level);

    for (;;) {
        uint32_t elapsed_ms = (uint32_t)((xTaskGetTickCount() - t_start) * portTICK_PERIOD_MS);

        if (level == 1 && idle_run >= EPD_BUSY_IDLE_CONFIRM_SAMPLES) {
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
            if (level == 0) {
                t_busy_since = xTaskGetTickCount();
                idle_run = 0;
            } else {
                total_busy_ms += (uint32_t)((xTaskGetTickCount() - t_busy_since) * portTICK_PERIOD_MS);
                idle_run = 1;
            }
            last_level = level;
        } else if (level == 1) {
            idle_run++;
        }
    }
}

static void epd_reset(void)
{
    epd_rst(1);
    vTaskDelay(pdMS_TO_TICKS(200));
    epd_rst(0);
    vTaskDelay(pdMS_TO_TICKS(20));
    epd_rst(1);
    vTaskDelay(pdMS_TO_TICKS(200));
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

    // Fix round 1 (Finding A): il driver di riferimento Waveshare configura BUSY
    // riusando lo stesso gpio_config_t degli output CS/DC/RST, che ha
    // pull_up_en = GPIO_PULLUP_ENABLE (vedi epaper_gpio_init() in epaper_port.c:
    // il campo non viene mai azzerato prima della seconda gpio_config() per
    // l'ingresso BUSY). La nostra versione precedente disabilitava esplicitamente
    // il pull-up: se la scheda non ha un pull-up esterno su BUSY, il pin poteva
    // leggere "idle" (1) per flottaggio invece che per reale rilascio del
    // pannello, facendo sembrare riuscito un refresh che il controller e-Paper
    // non aveva ancora completato (o non aveva nemmeno iniziato). Riallineato
    // al riferimento abilitando il pull-up interno.
    // Nota fix round 4: la polarita' BUSY e' stata riportata a idle=HIGH(1)/
    // busy=LOW(0), quella del driver di riferimento (l'inversione del round 3
    // causava il boot loop). Il pull-up interno su BUSY resta abilitato, come
    // nel riferimento (epaper_gpio_init riusa la stessa struct con pull_up
    // ENABLE per l'ingresso BUSY): coerente e innocuo.
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

// Propaga il primo errore SPI incontrato invece di continuare a inviare byte
// dopo un guasto (Finding B, fix round 1).
#define EPD_TRY(expr)                     \
    do {                                  \
        esp_err_t _epd_err = (expr);      \
        if (_epd_err != ESP_OK) {         \
            return _epd_err;              \
        }                                 \
    } while (0)

// Sequenza di init pannello, portata da epaper_port_init() (Waveshare).
static esp_err_t epd_panel_init(void)
{
    EPD_TRY(epd_send_command(0x4D));
    EPD_TRY(epd_send_data(0x78));

    EPD_TRY(epd_send_command(0x00)); // PSR
    EPD_TRY(epd_send_data(0x0F));
    EPD_TRY(epd_send_data(0x29));

    EPD_TRY(epd_send_command(0x06)); // BTST_P
    EPD_TRY(epd_send_data(0x0D));
    EPD_TRY(epd_send_data(0x12));
    EPD_TRY(epd_send_data(0x30));
    EPD_TRY(epd_send_data(0x20));
    EPD_TRY(epd_send_data(0x19));
    EPD_TRY(epd_send_data(0x2A));
    EPD_TRY(epd_send_data(0x22));

    EPD_TRY(epd_send_command(0x50)); // CDI
    EPD_TRY(epd_send_data(0x37));

    EPD_TRY(epd_send_command(0x61)); // TRES: risoluzione pannello
    EPD_TRY(epd_send_data(DISPLAY_W / 256));
    EPD_TRY(epd_send_data(DISPLAY_W % 256));
    EPD_TRY(epd_send_data(DISPLAY_H / 256));
    EPD_TRY(epd_send_data(DISPLAY_H % 256));

    EPD_TRY(epd_send_command(0xE9));
    EPD_TRY(epd_send_data(0x01));

    EPD_TRY(epd_send_command(0x30)); // PLL
    EPD_TRY(epd_send_data(0x08));

    EPD_TRY(epd_send_command(0x04)); // Power on
    return epd_wait_busy("power-on");
}

static esp_err_t epd_turn_on_display(void)
{
    EPD_TRY(epd_send_command(0x12)); // DISPLAY_REFRESH
    EPD_TRY(epd_send_data(0x00));
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
    // Fix round 4: pull-up abilitato su PWR come nel riferimento
    // (epaper_power_up in user_app.cpp: gpio6 output, pull_up ENABLE, set 0).
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

    // Fix round 4: ordine di init allineato al riferimento Waveshare
    // (epaper_port_init: prima epaper_spi_init(), poi epaper_gpio_init(), poi
    // reset). In Task 3 l'ordine era invertito (gpio poi spi): riportato
    // all'ordine provato del riferimento per eliminare ogni discrepanza nel
    // percorso di bring-up.
    ESP_LOGI(TAG, "epd: SPI + GPIO init (host=%d)", (int)BOARD_EPD_SPI_HOST);
    err = epd_spi_init();
    if (err != ESP_OK) {
        return err;
    }
    err = epd_gpio_init();
    if (err != ESP_OK) {
        return err;
    }

    // Fix round 2: livello raw di BUSY prima di inviare qualsiasi comando SPI
    // al pannello (subito dopo la config GPIO, prima di reset/init). Utile per
    // capire lo stato di riposo "naturale" della linea prima che qualunque
    // comando la tocchi. Con la polarita' di riferimento (idle=HIGH) un valore
    // stabile a 1 qui indica linea a riposo/idle.
    ESP_LOGI(TAG, "epd: BUSY livello raw all'avvio (prima di reset/init) = %d",
             gpio_get_level(BOARD_EPD_BUSY));

    ESP_LOGI(TAG, "epd: reset");
    epd_reset();

    ESP_LOGI(TAG, "epd: panel init sequence");
    err = epd_panel_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: panel init failed: %s", esp_err_to_name(err));
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

    const int src_stride = DISPLAY_W / 8;             // 25 byte/riga sorgente (1bpp)
    const int packed_w = DISPLAY_W / 4;                // 50 byte/riga verso il pannello (2bpp)

    ESP_LOGI(TAG, "epd: full refresh - invio framebuffer %dx%d", w, h);
    esp_err_t err = epd_send_command(0x10); // DATA_START_TRANSMISSION
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd: full refresh aborted, DATA_START_TRANSMISSION failed: %s",
                 esp_err_to_name(err));
        return;
    }
    for (int y = 0; y < DISPLAY_H && err == ESP_OK; y++) {
        for (int gx = 0; gx < packed_w; gx++) {
            uint8_t out = 0;
            for (int k = 0; k < 4; k++) {
                int x = gx * 4 + k;
                int bit = (buf[y * src_stride + (x / 8)] >> (7 - (x % 8))) & 0x1;
                uint8_t color = bit ? EPD_COLOR_BLACK : EPD_COLOR_WHITE;
                out |= (uint8_t)((color & 0x3) << (6 - k * 2));
            }
            err = epd_send_data(out);
            if (err != ESP_OK) {
                break; // interrompi subito: niente senso continuare a scrivere dopo un guasto SPI
            }
        }
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
