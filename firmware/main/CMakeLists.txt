idf_component_register(
    SRCS "app_main.c" "fw_version.c" "display.c" "power.c" "storage.c" "audio.c" "sensors.c" "wifi.c"
         "ota_manifest.c" "ota.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "../version.txt"
)
