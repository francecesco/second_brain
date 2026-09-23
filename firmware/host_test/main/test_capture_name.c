#include "unity.h"
#include "capture_name.h"
#include <string.h>
#include <time.h>

static struct tm tm_at(int y, int mo, int d, int h, int mi, int s) {
    struct tm t; memset(&t, 0, sizeof(t));
    t.tm_year = y - 1900; t.tm_mon = mo - 1; t.tm_mday = d;
    t.tm_hour = h; t.tm_min = mi; t.tm_sec = s;
    return t;
}

void test_capture_name_from_tm(void) {
    struct tm t = tm_at(2026, 9, 23, 19, 15, 30);
    char out[CAPTURE_NAME_MAX];
    capture_name_from_tm(&t, out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("cap_20260923_191530", out);
}

void test_capture_name_unsynced(void) {
    char out[CAPTURE_NAME_MAX];
    capture_name_unsynced(42, out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("cap_unsynced_000042", out);
    capture_name_unsynced(1234567, out, sizeof(out)); // oltre 6 cifre: non tronca
    TEST_ASSERT_EQUAL_STRING("cap_unsynced_1234567", out);
}

void test_capture_name_rtc_valid(void) {
    struct tm bad = tm_at(2000, 1, 1, 0, 0, 0);
    struct tm ok = tm_at(2024, 1, 1, 0, 0, 0);
    TEST_ASSERT_FALSE(capture_name_rtc_valid(&bad));
    TEST_ASSERT_TRUE(capture_name_rtc_valid(&ok));
}

void test_capture_name_with_suffix(void) {
    char out[CAPTURE_NAME_MAX];
    capture_name_with_suffix("cap_20260923_191530", 2, out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("cap_20260923_191530_2", out);
}
