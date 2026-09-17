#pragma once
#include <stdbool.h>

typedef struct {
    char version[16];
    char url[256];
    char sha256[65];
} ota_manifest_t;

bool ota_manifest_parse(const char *json, ota_manifest_t *out);
int  ota_semver_cmp(const char *a, const char *b);
bool ota_should_update(const char *current, const ota_manifest_t *m);
