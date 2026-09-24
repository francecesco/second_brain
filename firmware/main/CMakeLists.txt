idf_component_register(
    SRCS "app_main.c" "fw_version.c" "display.c" "power.c" "storage.c" "audio.c" "sensors.c" "wifi.c"
         "ota_manifest.c" "ota.c" "wifi_bssid.c" "wifi_select.c" "wav.c" "capture_name.c" "queue_policy.c" "sync_policy.c" "timesync_policy.c"
         "capture.c" "queue.c" "board_i2c.c" "timesync.c" "sync.c" "status_http.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "../version.txt"
)
