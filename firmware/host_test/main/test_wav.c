#include "unity.h"
#include "wav.h"
#include <string.h>

void test_wav_header_build_layout(void) {
    uint8_t h[WAV_HEADER_SIZE];
    wav_header_build(h, 32000); // 1 s di audio
    TEST_ASSERT_EQUAL_MEMORY("RIFF", h, 4);
    TEST_ASSERT_EQUAL_MEMORY("WAVE", h + 8, 4);
    TEST_ASSERT_EQUAL_MEMORY("fmt ", h + 12, 4);
    TEST_ASSERT_EQUAL_MEMORY("data", h + 36, 4);
    uint32_t riff = h[4] | (h[5] << 8) | (h[6] << 16) | ((uint32_t)h[7] << 24);
    TEST_ASSERT_EQUAL_UINT32(36 + 32000, riff);
    TEST_ASSERT_EQUAL_UINT16(1, h[20] | (h[21] << 8));
    TEST_ASSERT_EQUAL_UINT16(1, h[22] | (h[23] << 8));
    uint32_t sr = h[24] | (h[25] << 8) | (h[26] << 16) | ((uint32_t)h[27] << 24);
    TEST_ASSERT_EQUAL_UINT32(16000, sr);
    uint32_t br = h[28] | (h[29] << 8) | (h[30] << 16) | ((uint32_t)h[31] << 24);
    TEST_ASSERT_EQUAL_UINT32(32000, br);
    TEST_ASSERT_EQUAL_UINT16(2, h[32] | (h[33] << 8));
    TEST_ASSERT_EQUAL_UINT16(16, h[34] | (h[35] << 8));
    uint32_t data = h[40] | (h[41] << 8) | (h[42] << 16) | ((uint32_t)h[43] << 24);
    TEST_ASSERT_EQUAL_UINT32(32000, data);
}

void test_wav_header_parse_roundtrip(void) {
    uint8_t h[WAV_HEADER_SIZE];
    wav_header_build(h, 123456);
    uint32_t data = 0;
    TEST_ASSERT_TRUE(wav_header_parse(h, &data));
    TEST_ASSERT_EQUAL_UINT32(123456, data);
}

void test_wav_header_parse_rejects_garbage(void) {
    uint8_t h[WAV_HEADER_SIZE];
    memset(h, 0, sizeof(h));
    uint32_t data = 0;
    TEST_ASSERT_FALSE(wav_header_parse(h, &data));
    wav_header_build(h, 10);
    h[0] = 'X'; // RIFF rotto
    TEST_ASSERT_FALSE(wav_header_parse(h, &data));
}

void test_wav_bytes_to_ms(void) {
    TEST_ASSERT_EQUAL_UINT32(0, wav_bytes_to_ms(0));
    TEST_ASSERT_EQUAL_UINT32(1000, wav_bytes_to_ms(32000));
    TEST_ASSERT_EQUAL_UINT32(7031, wav_bytes_to_ms(225000)); // 225000/32 = 7031.25
}
