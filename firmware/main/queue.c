#include "queue.h"
#include "storage.h"
#include "config.h"
#include "capture_name.h"
#include "queue_policy.h"
#include "wav.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>

#include "esp_log.h"
#include "nvs.h"

static const char *TAG = "queue";

#define QUEUE_ABS    STORAGE_MOUNT "/" SB_QUEUE_DIR
#define REJECTED_ABS STORAGE_MOUNT "/" SB_QUEUE_REJECTED_DIR

void queue_abs_path(const char *name, char *out, size_t n)
{
    snprintf(out, n, "%s/%s", QUEUE_ABS, name);
}

static bool ends_with(const char *s, const char *suf)
{
    size_t ls = strlen(s), lf = strlen(suf);
    return ls >= lf && strcmp(s + ls - lf, suf) == 0;
}

static int cmp_str(const void *a, const void *b) { return strcmp((const char *)a, (const char *)b); }

esp_err_t queue_init(void)
{
    if (!storage_mounted()) return ESP_ERR_INVALID_STATE;
    esp_err_t err = storage_mkdir_p(SB_QUEUE_REJECTED_DIR); // crea anche queue/
    if (err != ESP_OK) return err;
    int promoted = queue_recover();
    ESP_LOGI(TAG, "coda pronta (%d .part recuperati)", promoted);
    return ESP_OK;
}

// Patcha l'header di un .part con la dimensione reale e lo rinomina a .wav.
static esp_err_t promote_part(const char *part_abs, uint64_t size)
{
    FILE *f = fopen(part_abs, "r+b");
    if (!f) return ESP_FAIL;
    uint8_t hdr[WAV_HEADER_SIZE];
    wav_header_build(hdr, (uint32_t)(size - WAV_HEADER_SIZE));
    bool ok = fwrite(hdr, 1, sizeof(hdr), f) == sizeof(hdr);
    fclose(f);
    if (!ok) return ESP_FAIL;
    char wav_abs[QUEUE_PATH_MAX + 32];
    strlcpy(wav_abs, part_abs, sizeof(wav_abs));
    wav_abs[strlen(wav_abs) - strlen(".part")] = '\0';
    return rename(part_abs, wav_abs) == 0 ? ESP_OK : ESP_FAIL;
}

int queue_recover(void)
{
    DIR *d = opendir(QUEUE_ABS);
    if (!d) return 0;
    int promoted = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (!ends_with(e->d_name, ".wav.part")) continue;
        char abs[QUEUE_PATH_MAX + 32];
        queue_abs_path(e->d_name, abs, sizeof(abs));
        struct stat st;
        if (stat(abs, &st) != 0) continue;
        if (queue_part_decide((uint64_t)st.st_size) == QUEUE_PART_PROMOTE) {
            if (promote_part(abs, (uint64_t)st.st_size) == ESP_OK) {
                ESP_LOGW(TAG, "recuperata cattura interrotta: %s (%ld byte)", e->d_name, (long)st.st_size);
                promoted++;
            }
        } else {
            ESP_LOGW(TAG, "scartato .part troppo corto: %s (%ld byte)", e->d_name, (long)st.st_size);
            unlink(abs);
        }
    }
    closedir(d);
    return promoted;
}

static uint32_t next_unsynced_seq(void)
{
    nvs_handle_t h;
    uint32_t seq = 0;
    if (nvs_open(SB_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return (uint32_t)(time(NULL) & 0xFFFFFF);
    nvs_get_u32(h, SB_NVS_KEY_CAPSEQ, &seq); // assente -> 0
    seq++;
    nvs_set_u32(h, SB_NVS_KEY_CAPSEQ, seq);
    nvs_commit(h);
    nvs_close(h);
    return seq;
}

static bool exists(const char *abs) { struct stat st; return stat(abs, &st) == 0; }

esp_err_t queue_new_part_path(char *out_path, size_t n, char *out_id, size_t id_n)
{
    if (!storage_mounted()) return ESP_ERR_INVALID_STATE;
    time_t now = time(NULL);
    struct tm utc;
    gmtime_r(&now, &utc);
    char base[CAPTURE_NAME_MAX];
    if (capture_name_rtc_valid(&utc)) capture_name_from_tm(&utc, base, sizeof(base));
    else capture_name_unsynced(next_unsynced_seq(), base, sizeof(base));

    char id[CAPTURE_NAME_MAX + 8];
    strlcpy(id, base, sizeof(id));
    for (int k = 2; k < 100; k++) {
        char wav_abs[QUEUE_PATH_MAX + 32], part_abs[QUEUE_PATH_MAX + 32];
        snprintf(wav_abs, sizeof(wav_abs), "%s/%s.wav", QUEUE_ABS, id);
        snprintf(part_abs, sizeof(part_abs), "%s/%s.wav.part", QUEUE_ABS, id);
        if (!exists(wav_abs) && !exists(part_abs)) {
            strlcpy(out_path, part_abs, n);
            strlcpy(out_id, id, id_n);
            return ESP_OK;
        }
        capture_name_with_suffix(base, k, id, sizeof(id));
    }
    return ESP_FAIL;
}

esp_err_t queue_commit(const char *part_path)
{
    char wav_abs[QUEUE_PATH_MAX + 32];
    strlcpy(wav_abs, part_path, sizeof(wav_abs));
    if (!ends_with(wav_abs, ".part")) return ESP_ERR_INVALID_ARG;
    wav_abs[strlen(wav_abs) - strlen(".part")] = '\0';
    if (rename(part_path, wav_abs) != 0) {
        ESP_LOGE(TAG, "rename %s fallita", part_path);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "in coda: %s", wav_abs);
    return ESP_OK;
}

esp_err_t queue_discard(const char *part_path)
{
    return unlink(part_path) == 0 ? ESP_OK : ESP_FAIL;
}

int queue_list(char names[][QUEUE_PATH_MAX], int max)
{
    DIR *d = opendir(QUEUE_ABS);
    if (!d) return 0;
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL && n < max) {
        if (e->d_type == DT_DIR) continue;
        if (!ends_with(e->d_name, ".wav")) continue;
        strlcpy(names[n++], e->d_name, QUEUE_PATH_MAX);
    }
    closedir(d);
    qsort(names, n, QUEUE_PATH_MAX, cmp_str);
    return n;
}

int queue_count(uint64_t *out_total_bytes)
{
    DIR *d = opendir(QUEUE_ABS);
    if (!d) { if (out_total_bytes) *out_total_bytes = 0; return 0; }
    int n = 0; uint64_t total = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (e->d_type == DT_DIR || !ends_with(e->d_name, ".wav")) continue;
        n++;
        if (out_total_bytes) {
            char abs[QUEUE_PATH_MAX + 32]; struct stat st;
            queue_abs_path(e->d_name, abs, sizeof(abs));
            if (stat(abs, &st) == 0) total += (uint64_t)st.st_size;
        }
    }
    closedir(d);
    if (out_total_bytes) *out_total_bytes = total;
    return n;
}

esp_err_t queue_delete(const char *name)
{
    char abs[QUEUE_PATH_MAX + 32];
    queue_abs_path(name, abs, sizeof(abs));
    return unlink(abs) == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t queue_reject(const char *name)
{
    char from[QUEUE_PATH_MAX + 32], to[QUEUE_PATH_MAX + 48];
    queue_abs_path(name, from, sizeof(from));
    snprintf(to, sizeof(to), "%s/%s", REJECTED_ABS, name);
    unlink(to); // se esiste gia' un omonimo rifiutato, sovrascrivi
    return rename(from, to) == 0 ? ESP_OK : ESP_FAIL;
}
