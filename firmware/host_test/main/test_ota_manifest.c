#include "unity.h"
#include "ota_manifest.h"
#include <string.h>

void test_semver_cmp(void) {
    TEST_ASSERT_EQUAL_INT(-1, ota_semver_cmp("0.1.0", "0.2.0"));
    TEST_ASSERT_EQUAL_INT( 1, ota_semver_cmp("1.0.0", "0.9.9"));
    TEST_ASSERT_EQUAL_INT( 0, ota_semver_cmp("1.2.3", "1.2.3"));
}

void test_parse_valid(void) {
    const char *j = "{\"version\":\"0.2.0\",\"url\":\"http://h/f.bin\",\"sha256\":\"abc\"}";
    ota_manifest_t m;
    TEST_ASSERT_TRUE(ota_manifest_parse(j, &m));
    TEST_ASSERT_EQUAL_STRING("0.2.0", m.version);
    TEST_ASSERT_EQUAL_STRING("http://h/f.bin", m.url);
    TEST_ASSERT_EQUAL_STRING("abc", m.sha256);
}

void test_parse_malformed_returns_false(void) {
    ota_manifest_t m;
    TEST_ASSERT_FALSE(ota_manifest_parse("not json", &m));
}

void test_parse_oversized_field_returns_false(void) {
    // sha256[65] ha spazio per 64 caratteri + terminatore: una stringa piu'
    // lunga deve essere rifiutata, non troncata silenziosamente.
    const char *j =
        "{\"version\":\"0.2.0\",\"url\":\"http://h/f.bin\","
        "\"sha256\":\"0000000000000000000000000000000000000000000000000000000000000000000000\"}";
    ota_manifest_t m;
    TEST_ASSERT_FALSE(ota_manifest_parse(j, &m));
}

void test_parse_missing_field_returns_false(void) {
    const char *j = "{\"version\":\"1.0.0\"}";
    ota_manifest_t m;
    TEST_ASSERT_FALSE(ota_manifest_parse(j, &m));
}

void test_should_update(void) {
    ota_manifest_t m; strcpy(m.version, "0.2.0");
    TEST_ASSERT_TRUE(ota_should_update("0.1.0", &m));
    TEST_ASSERT_FALSE(ota_should_update("0.2.0", &m)); // uguale
    TEST_ASSERT_FALSE(ota_should_update("0.3.0", &m)); // piu' vecchio nel manifest
}

void test_bssid_parse_valid(void);
void test_bssid_parse_uppercase_and_dash(void);
void test_bssid_parse_rejects_garbage(void);

void test_wav_header_build_layout(void);
void test_wav_header_parse_roundtrip(void);
void test_wav_header_parse_rejects_garbage(void);
void test_wav_bytes_to_ms(void);

void test_capture_name_from_tm(void);
void test_capture_name_unsynced(void);
void test_capture_name_rtc_valid(void);
void test_capture_name_with_suffix(void);

void test_queue_part_decide(void);
void test_sync_decide(void);
void test_timesync_needed(void);
void test_timesync_tm_to_epoch_utc(void);

void test_wifi_select_none_visible(void);
void test_wifi_select_picks_office(void);
void test_wifi_select_home_requires_pinned_bssid(void);
void test_wifi_select_strongest_wins(void);

void test_battery_pct_from_mv(void);
void test_battery_source_usb_host(void);
void test_battery_source_charger_voltage(void);
void test_battery_source_no_battery(void);
void test_battery_source_rising_means_charging(void);

void app_main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_semver_cmp);
    RUN_TEST(test_parse_valid);
    RUN_TEST(test_parse_malformed_returns_false);
    RUN_TEST(test_parse_oversized_field_returns_false);
    RUN_TEST(test_parse_missing_field_returns_false);
    RUN_TEST(test_should_update);
    RUN_TEST(test_bssid_parse_valid);
    RUN_TEST(test_bssid_parse_uppercase_and_dash);
    RUN_TEST(test_bssid_parse_rejects_garbage);
    RUN_TEST(test_wav_header_build_layout);
    RUN_TEST(test_wav_header_parse_roundtrip);
    RUN_TEST(test_wav_header_parse_rejects_garbage);
    RUN_TEST(test_wav_bytes_to_ms);
    RUN_TEST(test_capture_name_from_tm);
    RUN_TEST(test_capture_name_unsynced);
    RUN_TEST(test_capture_name_rtc_valid);
    RUN_TEST(test_capture_name_with_suffix);
    RUN_TEST(test_queue_part_decide);
    RUN_TEST(test_sync_decide);
    RUN_TEST(test_timesync_needed);
    RUN_TEST(test_timesync_tm_to_epoch_utc);
    RUN_TEST(test_wifi_select_none_visible);
    RUN_TEST(test_wifi_select_picks_office);
    RUN_TEST(test_wifi_select_home_requires_pinned_bssid);
    RUN_TEST(test_wifi_select_strongest_wins);
    RUN_TEST(test_battery_pct_from_mv);
    RUN_TEST(test_battery_source_usb_host);
    RUN_TEST(test_battery_source_charger_voltage);
    RUN_TEST(test_battery_source_no_battery);
    RUN_TEST(test_battery_source_rising_means_charging);
    UNITY_END();
}
