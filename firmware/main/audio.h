#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>

#define AUDIO_SAMPLE_RATE 16000

// Inizializza il ramo audio: power-enable, I2C per il codec ES8311, ES8311
// configurato a 16 kHz mono 16-bit, canale I2S RX standard sui pin BOARD_I2S_*.
esp_err_t audio_init(void);

// Legge fino a `bytes` byte di PCM 16-bit mono dal canale I2S RX in buf (allocato dal
// chiamante, sull'heap). Blocca al massimo timeout_ms. out_bytes = byte effettivi.
esp_err_t audio_read_block(int16_t *buf, size_t bytes, size_t *out_bytes, uint32_t timeout_ms);
