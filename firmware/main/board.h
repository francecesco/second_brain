#pragma once
#include "driver/spi_master.h"  // SPI2_HOST / SPI3_HOST
#include "driver/i2c.h"         // I2C_NUM_0 (i2c_port_t)
#include "esp_adc/adc_oneshot.h" // ADC_CHANNEL_3 (adc_channel_t)
// Pin-map Waveshare ESP32-S3-ePaper-1.54G (modulo ESP32-S3-PICO-1-N8R8) — UNICA fonte dei GPIO.
//
// Valori ricavati da: github.com/waveshareteam/ESP32-S3-ePaper-1.54G (clone di riferimento,
// non incluso nel repo), esempi in Example/ESP-IDF_5.5.1/*, in particolare:
//   - 09_E_Paper_Test/components/epaper_port/epaper_port.c (e-Paper SPI)
//   - 02_I2C_PCF85063, 03_I2C_SHTC3 /main/user_config.h + i2c_bsp.c (I2C SHTC3+RTC)
//   - 07_Audio_Test/components/codec_board/board_cfg.txt, voce "Board: S3_ePaper_1_54" (I2S ES8311)
//   - 04_SD_Card e 09_E_Paper_Test /components/sdcard_bsp/sdcard_bsp.c (microSD, SDMMC 1-bit)
//   - 01_ADC_Test/components/adc_bsp/adc_bsp.c (ADC batteria)
//   - 08_BATT_PWR_Test/components/button_bsp/button_bsp.c (tasti)
//   - 08_BATT_PWR_Test/components/board_power_bsp/board_power_bsp.cpp (enable di alimentazione)
// Il README del repo Waveshare conferma il modulo ESP32-S3-PICO-1-N8R8, coerente con la
// nostra scheda (ESP32-S3-PICO-1): nessuna discrepanza di variante.

// e-Paper (SPI) — CS/DC/RST pilotati via GPIO manuale (spics_io_num = -1 nel driver di
// riferimento), non tramite CS hardware dell'SPI driver.
//
// Redo (SSD1681 B/W): la scheda reale è la Waveshare "ESP32-S3 1.54inch e-Paper
// Dev Board" B/N (pannello GDEH0154D67, controller SSD1681), repo di riferimento
// corretto github.com/waveshareteam/ESP32-S3-ePaper-1.54 (NON il repo "-1.54G" a
// 4 colori usato per errore in precedenza). Da
// 02_Example/ESP-IDF/V2/12_RTC_Sleep_Test/main/user_config.h: EPD_SPI_NUM = SPI2_HOST.
#define BOARD_EPD_SPI_HOST   SPI2_HOST
#define BOARD_EPD_SCK        /* GPIO */ 12
#define BOARD_EPD_MOSI       /* GPIO */ 13
#define BOARD_EPD_CS         /* GPIO */ 11
#define BOARD_EPD_DC         /* GPIO */ 10
#define BOARD_EPD_RST        /* GPIO */ 9
#define BOARD_EPD_BUSY       /* GPIO */ 8
#define BOARD_EPD_PWR        /* GPIO */ 6     // enable alimentazione e-Paper, attivo basso (0=ON, 1=OFF)

// I2C (SHTC3 + RTC PCF85063, e bus condiviso anche dal codec ES8311)
#define BOARD_I2C_PORT       I2C_NUM_0
#define BOARD_I2C_SDA        /* GPIO */ 47
#define BOARD_I2C_SCL        /* GPIO */ 48
#define BOARD_SHTC3_ADDR     0x70
#define BOARD_PCF85063_ADDR  0x51

// I2S (codec ES8311) — pin-map board_cfg.txt voce "S3_ePaper_1_54": i2s: {bclk: 15, ws: 38,
// dout: 45, din: 16, mclk: 14}; out: {codec: ES8311, pa: 46, use_mclk: 1}
#define BOARD_ES8311_ADDR    0x18
#define BOARD_I2S_MCLK       /* GPIO */ 14
#define BOARD_I2S_BCLK       /* GPIO */ 15
#define BOARD_I2S_WS         /* GPIO */ 38
#define BOARD_I2S_DOUT       /* GPIO */ 45
#define BOARD_I2S_DIN        /* GPIO */ 16
#define BOARD_AUDIO_PA_EN    /* GPIO */ 46    // enable amplificatore altoparlante. ATTIVO ALTO (1=ON, 0=OFF):
                                               // verificato il 2026-09-24 (con 1 il beep in loop era udibile, con 0 silenzio)
#define BOARD_AUDIO_PA_ON    1
#define BOARD_AUDIO_PA_OFF   0
#define BOARD_AUDIO_PWR      /* GPIO */ 42    // enable ramo alimentazione audio (board_power_bsp), attivo basso

// microSD — la scheda usa il periferico SDMMC a 1 linea (CLK/CMD/D0), NON SPI.
// Confermato identico in 04_SD_Card e 09_E_Paper_Test/components/sdcard_bsp/sdcard_bsp.c.
#define BOARD_SD_USE_SDMMC   1
#define BOARD_SD_CLK         /* GPIO */ 39
#define BOARD_SD_CMD         /* GPIO */ 41
#define BOARD_SD_D0          /* GPIO */ 40
// Macro SPI-style non usate su questa scheda (mantenute per compatibilità di interfaccia,
// non collegate a hardware reale): se in futuro serve un percorso SPI per la SD, ridefinire.
#define BOARD_SD_CS          /* GPIO */ -1
#define BOARD_SD_SCK         /* GPIO */ -1
#define BOARD_SD_MOSI        /* GPIO */ -1
#define BOARD_SD_MISO        /* GPIO */ -1

// Batteria (ADC) e tasti
// adc_bsp.c usa ADC_UNIT_1 canale ADC_CHANNEL_3, che su ESP32-S3 corrisponde a GPIO4.
// Il calcolo "value = 0.001 * vol * 2" nel firmware di riferimento conferma un partitore 2:1.
#define BOARD_BAT_ADC_CHAN   /* adc_channel_t */ ADC_CHANNEL_3   // GPIO4
#define BOARD_BAT_DIVIDER    2.0f            // partitore 2:1, confermato da adc_bsp.c di riferimento
#define BOARD_BAT_LATCH      /* GPIO */ 17    // BAT_Control (schema Waveshare): MANTIENE l'alimentazione da batteria.
                                               // A batteria il tasto PWR accende la scheda solo finche' e' premuto;
                                               // il firmware deve portare questo pin a 1 subito e tenerlo alto (anche
                                               // in deep sleep, con hold). 0 = spegnimento fisico. Il partitore di
                                               // BAT_ADC (200K/200K su VBAT) e' sempre collegato: non serve abilitarlo.
#define BOARD_BTN_USER       /* GPIO */ 0     // BOOT_BUTTON_PIN: tasto utente generico (capture/wake); NON usato per
                                               // dev-mode perche' e' anche il pin di strapping BOOT (vedi power.c)
#define BOARD_BTN_ACTIVE_LOW 1
#define BOARD_BTN_PWR        /* GPIO */ 18    // PWR_BUTTON_PIN: tasto power/dedicato (attivo basso, pull-up interno);
                                               // usato come trigger dev-mode al boot, non e' un pin di strapping
