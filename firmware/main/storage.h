#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>

#define STORAGE_MOUNT "/sdcard"

esp_err_t storage_mount(void);
esp_err_t storage_write(const char *relpath, const uint8_t *data, size_t len);
esp_err_t storage_read(const char *relpath, uint8_t *buf, size_t buflen, size_t *out_len);
