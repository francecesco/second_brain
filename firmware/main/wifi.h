#pragma once
#include "esp_err.h"
#include <stddef.h>

// Inizializza NVS/netif/event loop, fa una scansione, sceglie tra le reti di
// WIFI_NETWORKS (secrets.h) quella presente con il segnale migliore e si collega,
// attendendo l'IP fino a timeout_ms dopo la scansione (che dura ~2.5 s). ESP_ERR_NOT_FOUND se nessuna rete
// configurata e' in vista, ESP_ERR_TIMEOUT se la connessione non arriva in tempo.
esp_err_t wifi_connect(int timeout_ms);

// Copia in out (buffer di almeno len byte) l'IP ottenuto da wifi_connect,
// come stringa "a.b.c.d". Ritorna ESP_ERR_INVALID_STATE se non ancora connesso.
esp_err_t wifi_get_ip(char *out, size_t len);

// Base URL del server per la rete a cui si e' connessi (campo server di WIFI_NETWORKS),
// es. "http://192.168.1.28:8000". NULL se non connessi.
const char *wifi_server_base_url(void);

// SSID della rete scelta (per i log). NULL se non connessi.
const char *wifi_current_ssid(void);
