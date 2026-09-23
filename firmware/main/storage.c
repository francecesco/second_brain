#include "storage.h"
#include "board.h"

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

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

bool storage_mounted(void) { return s_card != NULL; }

esp_err_t storage_free_bytes(uint64_t *out_free)
{
    if (!s_card) return ESP_ERR_INVALID_STATE;
    if (!out_free) return ESP_ERR_INVALID_ARG;
    uint64_t total = 0, free_b = 0;
    esp_err_t err = esp_vfs_fat_info(STORAGE_MOUNT, &total, &free_b);
    if (err != ESP_OK) return err;
    *out_free = free_b;
    return ESP_OK;
}

esp_err_t storage_mkdir_p(const char *relpath)
{
    if (!s_card) return ESP_ERR_INVALID_STATE;
    char path[160];
    int n = snprintf(path, sizeof(path), "%s/%s", STORAGE_MOUNT, relpath);
    if (n < 0 || (size_t)n >= sizeof(path)) return ESP_ERR_INVALID_ARG;
    // crea ogni livello dopo il mount point
    for (char *p = path + strlen(STORAGE_MOUNT) + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (mkdir(path, 0775) != 0 && errno != EEXIST) return ESP_FAIL;
            *p = '/';
        }
    }
    if (mkdir(path, 0775) != 0 && errno != EEXIST) {
        ESP_LOGE(TAG, "mkdir %s fallita: errno %d", path, errno);
        return ESP_FAIL;
    }
    return ESP_OK;
}
