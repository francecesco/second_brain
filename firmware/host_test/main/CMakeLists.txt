idf_component_register(
    SRCS "test_ota_manifest.c" "test_wifi_bssid.c" "test_wifi_select.c" "../../main/wifi_select.c" "test_wav.c" "../../main/wav.c" "test_capture_name.c" "../../main/capture_name.c"
         "test_policies.c" "../../main/queue_policy.c" "../../main/sync_policy.c" "../../main/timesync_policy.c" "../../main/ota_manifest.c" "../../main/wifi_bssid.c"
    INCLUDE_DIRS "../../main"
    REQUIRES unity json
)
