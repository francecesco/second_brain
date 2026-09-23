idf_component_register(
    SRCS "app_main.c" "fw_version.c" "display.c" "power.c" "storage.c" "audio.c" "sensors.c" "wifi.c"
         "ota_manifest.c" "ota.c" "wifi_bssid.c" "wav.c" "capture_name.c" "queue_policy.c" "sync_policy.c" "timesync_policy.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "../version.txt"
)
