#pragma once
#include <stdbool.h>
#include <stdint.h>

// Header WAV canonico PCM 16 kHz mono 16 bit (44 byte). Funzioni pure, testate su host.
#define WAV_HEADER_SIZE   44
#define WAV_SAMPLE_RATE   16000u
#define WAV_BYTES_PER_SEC 32000u   // 16000 Hz * 1 canale * 2 byte

// Scrive in out i 44 byte dell'header per data_bytes byte di PCM.
void wav_header_build(uint8_t out[WAV_HEADER_SIZE], uint32_t data_bytes);

// Verifica RIFF/WAVE/fmt /data e ritorna la dimensione dichiarata del chunk data.
bool wav_header_parse(const uint8_t in[WAV_HEADER_SIZE], uint32_t *data_bytes);

// Durata in millisecondi di data_bytes byte di PCM a 16 kHz mono 16 bit.
uint32_t wav_bytes_to_ms(uint32_t data_bytes);
