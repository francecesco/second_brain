idf_component_register(
    SRCS "test_ota_manifest.c" "../../main/ota_manifest.c"
    INCLUDE_DIRS "../../main"
    REQUIRES unity json
)
