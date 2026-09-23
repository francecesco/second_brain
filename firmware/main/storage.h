#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define STORAGE_MOUNT "/sdcard"

esp_err_t storage_mount(void);                       // idempotente
bool      storage_mounted(void);
esp_err_t storage_free_bytes(uint64_t *out_free);    // spazio libero sul volume
esp_err_t storage_mkdir_p(const char *relpath);      // crea STORAGE_MOUNT/relpath (un livello alla volta)
