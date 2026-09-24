#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define AUDIO_SAMPLE_RATE 16000

// Inizializza il ramo audio: power-enable, I2C per il codec ES8311, ES8311
// configurato a 16 kHz mono 16-bit, canale I2S RX standard sui pin BOARD_I2S_*.
esp_err_t audio_init(void);

// Legge fino a `bytes` byte di PCM 16-bit mono dal canale I2S RX in buf (allocato dal
// chiamante, sull'heap). Blocca al massimo timeout_ms. out_bytes = byte effettivi.
esp_err_t audio_read_block(int16_t *buf, size_t bytes, size_t *out_bytes, uint32_t timeout_ms);

// Suona un tono sinusoidale (freq_hz, duration_ms) sull'altoparlante esterno (header
// MX1.25 "speaker" della scheda) accendendo l'amplificatore solo per la durata del suono.
// Blocca per ~duration_ms. Richiede audio_init().
esp_err_t audio_beep(int freq_hz, int duration_ms);

// Accende/spegne l'amplificatore dell'altoparlante. Acceso in anticipo (es. all'inizio di
// una cattura) evita l'attesa di avvio a ogni beep. Il DAC resta muto fuori dai beep.
void audio_amp_enable(bool on);
