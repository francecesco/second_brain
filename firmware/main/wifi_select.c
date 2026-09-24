#include "wifi_select.h"
#include "wifi_bssid.h"
#include <string.h>

int wifi_select_best(const wifi_network_t *nets, int n_nets, const wifi_scan_entry_t *scan, int n_scan)
{
    int best = -1;
    int best_rssi = -128;
    for (int i = 0; i < n_nets; i++) {
        uint8_t want[6];
        bool pin = nets[i].bssid && nets[i].bssid[0] && wifi_parse_bssid(nets[i].bssid, want);
        for (int j = 0; j < n_scan; j++) {
            if (strcmp(nets[i].ssid, scan[j].ssid) != 0) continue;
            if (pin && memcmp(want, scan[j].bssid, 6) != 0) continue;
            if (scan[j].rssi > best_rssi) { best_rssi = scan[j].rssi; best = i; }
        }
    }
    return best;
}
