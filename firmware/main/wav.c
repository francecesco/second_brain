#include "wav.h"
#include <string.h>

static void put_u16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
static void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = v >> 24;
}
static uint32_t get_u32(const uint8_t *p)
{
    return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24);
}

void wav_header_build(uint8_t out[WAV_HEADER_SIZE], uint32_t data_bytes)
{
    memcpy(out + 0, "RIFF", 4);
    put_u32(out + 4, 36 + data_bytes);
    memcpy(out + 8, "WAVE", 4);
    memcpy(out + 12, "fmt ", 4);
    put_u32(out + 16, 16);              // fmt chunk size
    put_u16(out + 20, 1);               // PCM
    put_u16(out + 22, 1);               // mono
    put_u32(out + 24, WAV_SAMPLE_RATE);
    put_u32(out + 28, WAV_BYTES_PER_SEC);
    put_u16(out + 32, 2);               // block align
    put_u16(out + 34, 16);              // bits per sample
    memcpy(out + 36, "data", 4);
    put_u32(out + 40, data_bytes);
}

bool wav_header_parse(const uint8_t in[WAV_HEADER_SIZE], uint32_t *data_bytes)
{
    if (!in || !data_bytes) return false;
    if (memcmp(in + 0, "RIFF", 4) != 0) return false;
    if (memcmp(in + 8, "WAVE", 4) != 0) return false;
    if (memcmp(in + 12, "fmt ", 4) != 0) return false;
    if (memcmp(in + 36, "data", 4) != 0) return false;
    *data_bytes = get_u32(in + 40);
    return true;
}

uint32_t wav_bytes_to_ms(uint32_t data_bytes)
{
    return (uint32_t)(((uint64_t)data_bytes * 1000u) / WAV_BYTES_PER_SEC);
}
