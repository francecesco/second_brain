#pragma once
#include "esp_err.h"

#define AUDIO_SAMPLE_RATE 16000

// Inizializza il ramo audio: power-enable, I2C per il codec ES8311, ES8311
// configurato a 16 kHz mono 16-bit, canale I2S RX standard sui pin BOARD_I2S_*.
esp_err_t audio_init(void);

// Registra `seconds` secondi di audio (16 kHz, mono, PCM 16-bit) e li salva come
// WAV (header 44 byte) in STORAGE_MOUNT/relpath sulla microSD. Richiede una SD
// gia' montata (storage_mount()) e audio_init() gia' chiamato.
esp_err_t audio_record_wav(const char *relpath, int seconds);
