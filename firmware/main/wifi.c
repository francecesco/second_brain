#include "wifi.h"
#include "wifi_bssid.h"
#include "wifi_select.h"
#include "secrets.h"
#include "esp_timer.h"

#include <string.h>
#include <stdlib.h>

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

static const wifi_network_t s_nets[] = WIFI_NETWORKS;
#define WIFI_N_NETS (int)(sizeof(s_nets) / sizeof(s_nets[0]))
static int s_selected = -1;
#define WIFI_SCAN_MAX_AP 20

static void event_handler(void *arg, esp_event_base_t event_base,
                           int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
        wifi_event_sta_connected_t *ev = (wifi_event_sta_connected_t *)event_data;
        ESP_LOGI(TAG, "associato a %02x:%02x:%02x:%02x:%02x:%02x ch%d, attendo IP (DHCP)...",
                 ev->bssid[0], ev->bssid[1], ev->bssid[2], ev->bssid[3], ev->bssid[4], ev->bssid[5], ev->channel);
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *ev = (wifi_event_sta_disconnected_t *)event_data;
        ESP_LOGW(TAG, "disconnesso, reason=%d rssi=%d", ev->reason, ev->rssi);
        // Ritenta finche' non scade il timeout esterno (solo dopo la scelta della rete).
        if (s_selected >= 0) esp_wifi_connect();
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

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    // --- scansione: quali reti configurate sono in vista? ---
    int64_t t0 = esp_timer_get_time();
    wifi_scan_config_t scan_cfg = { .show_hidden = false, .scan_type = WIFI_SCAN_TYPE_ACTIVE };
    esp_err_t err_scan = esp_wifi_scan_start(&scan_cfg, true /* blocking */);
    uint16_t n_ap = 0;
    wifi_ap_record_t *recs = NULL;
    wifi_scan_entry_t *entries = NULL;
    int n_entries = 0;
    if (err_scan == ESP_OK) {
        esp_wifi_scan_get_ap_num(&n_ap);
        if (n_ap > WIFI_SCAN_MAX_AP) n_ap = WIFI_SCAN_MAX_AP;
        recs = calloc(n_ap ? n_ap : 1, sizeof(wifi_ap_record_t));
        entries = calloc(n_ap ? n_ap : 1, sizeof(wifi_scan_entry_t));
        if (recs && entries && esp_wifi_scan_get_ap_records(&n_ap, recs) == ESP_OK) {
            for (int i = 0; i < n_ap; i++) {
                strlcpy(entries[i].ssid, (const char *)recs[i].ssid, sizeof(entries[i].ssid));
                memcpy(entries[i].bssid, recs[i].bssid, 6);
                entries[i].rssi = recs[i].rssi;
            }
            n_entries = n_ap;
        }
    } else {
        ESP_LOGW(TAG, "scan fallita: %s", esp_err_to_name(err_scan));
    }
    s_selected = wifi_select_best(s_nets, WIFI_N_NETS, entries, n_entries);
    ESP_LOGI(TAG, "scan: %d AP in %lld ms, rete scelta: %s", n_entries,
             (long long)((esp_timer_get_time() - t0) / 1000),
             s_selected >= 0 ? s_nets[s_selected].ssid : "(nessuna configurata in vista)");
    free(recs);
    free(entries);
    if (s_selected < 0) {
        esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, instance_got_ip);
        esp_event_handler_instance_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, instance_any_id);
        return ESP_ERR_NOT_FOUND;
    }

    // --- connessione alla rete scelta ---
    const wifi_network_t *net = &s_nets[s_selected];
    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, net->ssid, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, net->password, sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    if (net->bssid && net->bssid[0]) {
        if (wifi_parse_bssid(net->bssid, wifi_config.sta.bssid)) {
            wifi_config.sta.bssid_set = true;
            ESP_LOGI(TAG, "BSSID forzato: %s", net->bssid);
        } else {
            ESP_LOGE(TAG, "BSSID '%s' non valido in secrets.h, ignorato", net->bssid);
        }
    }
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_LOGI(TAG, "connecting to SSID '%s'...", net->ssid);
    esp_wifi_connect();

    // Il budget timeout_ms vale per la sola connessione: la scansione (~2.5 s) non lo
    // consuma, altrimenti in ambienti affollati la connessione non ce la fa.

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

const char *wifi_server_base_url(void)
{
    if (!s_got_ip || s_selected < 0) return NULL;
    return s_nets[s_selected].server;
}

const char *wifi_current_ssid(void)
{
    if (!s_got_ip || s_selected < 0) return NULL;
    return s_nets[s_selected].ssid;
}
