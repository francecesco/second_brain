#include "storage.h"
#include "board.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"
#include "driver/sdmmc_host.h"

#if !BOARD_SD_USE_SDMMC
#error "storage.c: questa scheda usa la microSD via SDMMC 1-bit; board.h non lo conferma (BOARD_SD_USE_SDMMC != 1)"
#endif

static const char *TAG = "storage";

static sdmmc_card_t *s_card = NULL;

esp_err_t storage_mount(void)
{
    if (s_card != NULL) {
        ESP_LOGI(TAG, "SD gia' montata su %s", STORAGE_MOUNT);
        return ESP_OK;
    }

    esp_vfs_fat_sdmmc_mount_config_t mount_config = {
        .format_if_mount_failed = false,
        .max_files = 5,
        .allocation_unit_size = 16 * 1024,
    };

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_PROBING;

    sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();
    slot_config.width = 1;
    slot_config.clk = BOARD_SD_CLK;
    slot_config.cmd = BOARD_SD_CMD;
    slot_config.d0 = BOARD_SD_D0;

    esp_err_t err = esp_vfs_fat_sdmmc_mount(STORAGE_MOUNT, &host, &slot_config,
                                             &mount_config, &s_card);
    if (err != ESP_OK) {
        if (err == ESP_FAIL) {
            ESP_LOGE(TAG, "mount FAT fallito (scheda non formattata o non presente)");
        } else {
            ESP_LOGE(TAG, "init SDMMC fallita: %s", esp_err_to_name(err));
        }
        s_card = NULL;
        return err;
    }

    ESP_LOGI(TAG, "SD montata su %s (SDMMC 1-bit, clk=%d cmd=%d d0=%d)",
             STORAGE_MOUNT, BOARD_SD_CLK, BOARD_SD_CMD, BOARD_SD_D0);
    ESP_LOGI(TAG, "SD card: name=%s, size=%llu MB, speed=%s",
             s_card->cid.name,
             ((uint64_t)s_card->csd.capacity) * s_card->csd.sector_size / (1024ULL * 1024ULL),
             (s_card->real_freq_khz >= 1000) ? "high" : "default");

    return ESP_OK;
}

esp_err_t storage_write(const char *relpath, const uint8_t *data, size_t len)
{
    if (s_card == NULL) {
        ESP_LOGE(TAG, "storage_write: SD non montata");
        return ESP_ERR_INVALID_STATE;
    }
    if (relpath == NULL || (data == NULL && len > 0)) {
        return ESP_ERR_INVALID_ARG;
    }

    char path[300];
    int n = snprintf(path, sizeof(path), "%s/%s", STORAGE_MOUNT, relpath);
    if (n < 0 || (size_t)n >= sizeof(path)) {
        return ESP_ERR_INVALID_ARG;
    }

    FILE *f = fopen(path, "wb");
    if (f == NULL) {
        ESP_LOGE(TAG, "impossibile aprire %s in scrittura", path);
        return ESP_FAIL;
    }

    size_t written = fwrite(data, 1, len, f);
    fclose(f);

    if (written != len) {
        ESP_LOGE(TAG, "scrittura incompleta su %s: %u/%u byte", path,
                  (unsigned)written, (unsigned)len);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "scritti %u byte su %s", (unsigned)written, path);
    return ESP_OK;
}

esp_err_t storage_read(const char *relpath, uint8_t *buf, size_t buflen, size_t *out_len)
{
    if (s_card == NULL) {
        ESP_LOGE(TAG, "storage_read: SD non montata");
        return ESP_ERR_INVALID_STATE;
    }
    if (relpath == NULL || buf == NULL || out_len == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    char path[300];
    int n = snprintf(path, sizeof(path), "%s/%s", STORAGE_MOUNT, relpath);
    if (n < 0 || (size_t)n >= sizeof(path)) {
        return ESP_ERR_INVALID_ARG;
    }

    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        ESP_LOGE(TAG, "impossibile aprire %s in lettura", path);
        return ESP_ERR_NOT_FOUND;
    }

    size_t r = fread(buf, 1, buflen, f);
    fclose(f);

    *out_len = r;
    ESP_LOGI(TAG, "letti %u byte da %s", (unsigned)r, path);
    return ESP_OK;
}
