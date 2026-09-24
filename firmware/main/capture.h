#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

// Registratore a durata libera (spec §5.2): un task FreeRTOS legge l'I2S a blocchi da
// 128 ms e li appende al file; il chiamante decide quando fermare (rilascio tasto).
// Il file viene scritto come "<path>.wav.part" con header WAV provvisorio; capture_stop
// fa fsync, patcha l'header con i byte reali e chiude. Rinominare a .wav e' compito
// del chiamante (queue_commit), cosi' la decisione "scarta se < 1 s" resta fuori da qui.
typedef struct {
    uint32_t  data_bytes;   // PCM scritti
    uint32_t  duration_ms;  // = wav_bytes_to_ms(data_bytes)
    bool      hit_max;      // fermata dal limite SB_CAPTURE_MAX_MS
    esp_err_t err;          // ESP_OK oppure primo errore I2S/SD incontrato
    int       peak;         // picco assoluto dei campioni (0..32767): diagnostica livello microfono
} capture_result_t;

esp_err_t capture_start(const char *abs_path_part);  // richiede audio_init() e SD montata
esp_err_t capture_stop(capture_result_t *out);       // idempotente se non in corso
bool      capture_is_running(void);
uint32_t  capture_elapsed_ms(void);
