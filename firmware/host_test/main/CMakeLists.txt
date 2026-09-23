idf_component_register(
    SRCS "test_ota_manifest.c" "test_wifi_bssid.c" "../../main/ota_manifest.c" "../../main/wifi_bssid.c"
    INCLUDE_DIRS "../../main"
    REQUIRES unity json
)
