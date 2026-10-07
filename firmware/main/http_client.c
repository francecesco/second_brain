#include "http_client.h"
#include "secrets.h"
#include <stdio.h>
#include "esp_crt_bundle.h"
#include "esp_log.h"

static const char *TAG = "http";

esp_http_client_config_t http_client_config(const char *url, esp_http_client_method_t method,
                                            int timeout_ms)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .method = method,
        .timeout_ms = timeout_ms,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    return cfg;
}

void http_client_set_auth(esp_http_client_handle_t client)
{
    static const char token[] = DEVICE_TOKEN;
    if (token[0] == '\0') return;
    // "Bearer " + token: il token e' 43 caratteri (token_urlsafe(32)); 128 bastano con margine.
    // Il valore non va mai nei log.
    char value[128];
    int n = snprintf(value, sizeof(value), "Bearer %s", token);
    if (n < 0 || n >= (int)sizeof(value)) { ESP_LOGE(TAG, "DEVICE_TOKEN troppo lungo"); return; }
    esp_http_client_set_header(client, "Authorization", value);
}
