#pragma once
#include "esp_err.h"
#include <stddef.h>

// Inizializza NVS/netif/event loop, avvia lo STA con le credenziali di
// secrets.h e attende IP_EVENT_STA_GOT_IP fino a timeout_ms.
// Ritorna ESP_OK se ottiene l'IP, ESP_ERR_TIMEOUT se scade il timeout.
esp_err_t wifi_connect(int timeout_ms);

// Copia in out (buffer di almeno len byte) l'IP ottenuto da wifi_connect,
// come stringa "a.b.c.d". Ritorna ESP_ERR_INVALID_STATE se non ancora connesso.
esp_err_t wifi_get_ip(char *out, size_t len);
