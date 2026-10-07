#pragma once
#include "esp_http_client.h"
// Configurazione comune dei client HTTP verso il backend (spec HTTPS+token §5):
// bundle di certificati di ESP-IDF per gli URL https:// (ignorato per http://),
// header Authorization: Bearer con DEVICE_TOKEN se non e' vuoto.

// Config con url, metodo, timeout di inattivita' e bundle di certificati. Il chiamante
// la passa a esp_http_client_init.
esp_http_client_config_t http_client_config(const char *url, esp_http_client_method_t method,
                                            int timeout_ms);

// Aggiunge "Authorization: Bearer <DEVICE_TOKEN>" se il token non e' vuoto.
void http_client_set_auth(esp_http_client_handle_t client);
