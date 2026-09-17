idf_component_register(
    SRCS "app_main.c" "fw_version.c" "display.c" "power.c" "storage.c" "audio.c" "sensors.c" "wifi.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "../version.txt"
)
