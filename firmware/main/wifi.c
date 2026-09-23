#include "wifi.h"
#include "secrets.h"
#include "wifi_bssid.h"

#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

static const char *TAG = "wifi";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static EventGroupHandle_t s_wifi_event_group;
static char s_ip_str[16] = {0};
static bool s_got_ip = false;

static void event_handler(void *arg, esp_event_base_t event_base,
                           int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        // Ritenta finche' non scade il timeout esterno; nel frattempo non
        // segnaliamo fallimento definitivo qui.
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        snprintf(s_ip_str, sizeof(s_ip_str), IPSTR, IP2STR(&event->ip_info.ip));
        s_got_ip = true;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static esp_err_t nvs_init_safe(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err == ESP_ERR_INVALID_STATE) {
        // Gia' inizializzato da un altro modulo: va bene.
        err = ESP_OK;
    }
    return err;
}

esp_err_t wifi_connect(int timeout_ms)
{
    esp_err_t err = nvs_init_safe();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs init failed: %s", esp_err_to_name(err));
        return err;
    }

    err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    s_wifi_event_group = xEventGroupCreate();

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, &instance_got_ip));

    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, WIFI_SSID, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, WIFI_PASSWORD, sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
#ifdef WIFI_BSSID
    // BSSID forzato (opzionale, da secrets.h): sulla rete di sviluppo ci
    // sono piu' access point con lo stesso SSID e uno di questi non inoltra
    // le connessioni TCP da/verso i suoi client (OTA pull e push falliscono
    // con timeout). Con bssid_set il driver si associa SOLO a quell'AP: se
    // non e' raggiungibile la connessione fallisce invece di ripiegare
    // sull'altro.
    if (wifi_parse_bssid(WIFI_BSSID, wifi_config.sta.bssid)) {
        wifi_config.sta.bssid_set = true;
        ESP_LOGI(TAG, "BSSID forzato: %s", WIFI_BSSID);
    } else {
        ESP_LOGE(TAG, "WIFI_BSSID '%s' non valido in secrets.h, ignorato", WIFI_BSSID);
    }
#endif

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "connecting to SSID '%s'...", WIFI_SSID);

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                                            pdFALSE, pdFALSE,
                                            pdMS_TO_TICKS(timeout_ms));

    esp_err_t result;
    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "connected, ip=%s", s_ip_str);
        result = ESP_OK;
    } else {
        ESP_LOGW(TAG, "connect timeout after %d ms", timeout_ms);
        result = ESP_ERR_TIMEOUT;
    }

    esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, instance_got_ip);
    esp_event_handler_instance_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, instance_any_id);

    return result;
}

esp_err_t wifi_get_ip(char *out, size_t len)
{
    if (!s_got_ip) {
        return ESP_ERR_INVALID_STATE;
    }
    strlcpy(out, s_ip_str, len);
    return ESP_OK;
}
