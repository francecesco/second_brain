#pragma once
#include <stdbool.h>
#include <stdint.h>

// Scelta della rete tra quelle configurate (secrets.h) e quelle viste in scansione.
// Funzione pura, testata su host.
typedef struct {
    const char *ssid;
    const char *password;
    const char *bssid;    // "aa:bb:cc:dd:ee:ff" per forzare un AP, oppure NULL
    const char *server;   // base URL del backend/server di test su quella rete, es. "http://192.168.1.28:8000"
} wifi_network_t;

typedef struct {
    char    ssid[33];
    uint8_t bssid[6];
    int8_t  rssi;
} wifi_scan_entry_t;

// Ritorna l'indice in nets della rete configurata con il segnale migliore tra quelle
// presenti in scan (SSID uguale; se la voce ha un BSSID, deve coincidere anche quello),
// oppure -1 se nessuna e' in vista.
int wifi_select_best(const wifi_network_t *nets, int n_nets, const wifi_scan_entry_t *scan, int n_scan);
