#include "unity.h"
#include "wifi_bssid.h"
#include <string.h>

void test_bssid_parse_valid(void) {
    uint8_t out[6] = {0};
    TEST_ASSERT_TRUE(wifi_parse_bssid("54:78:f0:bd:65:eb", out));
    const uint8_t exp[6] = {0x54, 0x78, 0xf0, 0xbd, 0x65, 0xeb};
    TEST_ASSERT_EQUAL_MEMORY(exp, out, 6);
}

void test_bssid_parse_uppercase_and_dash(void) {
    uint8_t out[6] = {0};
    TEST_ASSERT_TRUE(wifi_parse_bssid("CC-2D-21-5F-59-29", out));
    const uint8_t exp[6] = {0xcc, 0x2d, 0x21, 0x5f, 0x59, 0x29};
    TEST_ASSERT_EQUAL_MEMORY(exp, out, 6);
}

void test_bssid_parse_rejects_garbage(void) {
    uint8_t out[6];
    TEST_ASSERT_FALSE(wifi_parse_bssid("", out));
    TEST_ASSERT_FALSE(wifi_parse_bssid("54:78:f0:bd:65", out));        // 5 ottetti
    TEST_ASSERT_FALSE(wifi_parse_bssid("54:78:f0:bd:65:eb:01", out));  // 7 ottetti
    TEST_ASSERT_FALSE(wifi_parse_bssid("54:78:g0:bd:65:eb", out));     // non-hex
    TEST_ASSERT_FALSE(wifi_parse_bssid("5478f0bd65eb", out));          // senza separatori
    TEST_ASSERT_FALSE(wifi_parse_bssid(NULL, out));
}
