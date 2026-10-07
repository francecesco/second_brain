#include "unity.h"
#include "wifi_select.h"
#include <string.h>

static wifi_scan_entry_t ap(const char *ssid, const char *bssid_txt, int rssi) {
    wifi_scan_entry_t e; memset(&e, 0, sizeof(e));
    strncpy(e.ssid, ssid, 32); e.rssi = (int8_t)rssi;
    unsigned b[6]; sscanf(bssid_txt, "%x:%x:%x:%x:%x:%x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]);
    for (int i = 0; i < 6; i++) e.bssid[i] = (uint8_t)b[i];
    return e;
}

static const wifi_network_t NETS[] = {
    { "Casa",    "pw1", "54:78:f0:bd:65:eb" },
    { "Ufficio", "pw2", NULL },
};

void test_wifi_select_none_visible(void) {
    wifi_scan_entry_t scan[] = { ap("Vicino", "00:11:22:33:44:55", -50) };
    TEST_ASSERT_EQUAL_INT(-1, wifi_select_best(NETS, 2, scan, 1));
    TEST_ASSERT_EQUAL_INT(-1, wifi_select_best(NETS, 2, scan, 0));
}

void test_wifi_select_picks_office(void) {
    wifi_scan_entry_t scan[] = { ap("Vicino", "00:11:22:33:44:55", -40), ap("Ufficio", "aa:aa:aa:aa:aa:aa", -70) };
    TEST_ASSERT_EQUAL_INT(1, wifi_select_best(NETS, 2, scan, 2));
}

void test_wifi_select_home_requires_pinned_bssid(void) {
    // Casa vista solo tramite l'altro AP (bssid diverso): NON va scelta.
    wifi_scan_entry_t scan1[] = { ap("Casa", "cc:2d:21:5f:59:29", -50) };
    TEST_ASSERT_EQUAL_INT(-1, wifi_select_best(NETS, 2, scan1, 1));
    // Casa vista dal router principale: scelta anche se piu' debole dell'altro AP.
    wifi_scan_entry_t scan2[] = { ap("Casa", "cc:2d:21:5f:59:29", -50), ap("Casa", "54:78:F0:BD:65:EB", -80) };
    TEST_ASSERT_EQUAL_INT(0, wifi_select_best(NETS, 2, scan2, 2));
}

void test_wifi_select_strongest_wins(void) {
    wifi_scan_entry_t scan[] = { ap("Ufficio", "aa:aa:aa:aa:aa:aa", -75), ap("Casa", "54:78:f0:bd:65:eb", -60) };
    TEST_ASSERT_EQUAL_INT(0, wifi_select_best(NETS, 2, scan, 2));
    scan[0].rssi = -50;
    TEST_ASSERT_EQUAL_INT(1, wifi_select_best(NETS, 2, scan, 2));
}
