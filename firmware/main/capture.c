#include "capture.h"
#include "audio.h"
#include "wav.h"
#include "config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "capture";

#define CAPTURE_BLOCK_BYTES     4096   // 128 ms a 32000 B/s
#define CAPTURE_TASK_STACK      4096
#define CAPTURE_TASK_PRIO       (tskIDLE_PRIORITY + 5)  // sopra il main (1)
#define CAPTURE_READ_TIMEOUT_MS 500
#define CAPTURE_STOP_WAIT_MS    3000

typedef struct {
    FILE *f;
    volatile bool stop_req;
    volatile bool running;
    volatile bool hit_max;
    volatile esp_err_t err;
    volatile uint32_t data_bytes;
    int64_t start_us;
    SemaphoreHandle_t done;
} capture_ctx_t;

static capture_ctx_t s_ctx = {0};

static void capture_task(void *arg)
{
    capture_ctx_t *c = (capture_ctx_t *)arg;
    const uint32_t max_bytes = (uint32_t)(((uint64_t)WAV_BYTES_PER_SEC * SB_CAPTURE_MAX_MS) / 1000u);
    int16_t *buf = malloc(CAPTURE_BLOCK_BYTES);
    if (!buf) {
        c->err = ESP_ERR_NO_MEM;
    } else {
        while (!c->stop_req) {
            if (c->data_bytes >= max_bytes) { c->hit_max = true; break; }
            size_t want = CAPTURE_BLOCK_BYTES;
            if (max_bytes - c->data_bytes < want) want = max_bytes - c->data_bytes;
            size_t got = 0;
            esp_err_t e = audio_read_block(buf, want, &got, CAPTURE_READ_TIMEOUT_MS);
            if (e != ESP_OK && e != ESP_ERR_TIMEOUT) { c->err = e; break; }
            if (got == 0) continue;
            if (fwrite(buf, 1, got, c->f) != got) {
                ESP_LOGE(TAG, "fwrite fallita a %lu byte", (unsigned long)c->data_bytes);
                c->err = ESP_FAIL;
                break;
            }
            c->data_bytes += (uint32_t)got;
        }
        free(buf);
    }
    c->running = false;
    xSemaphoreGive(c->done);
    vTaskDelete(NULL);
}

esp_err_t capture_start(const char *abs_path_part)
{
    if (s_ctx.running) return ESP_ERR_INVALID_STATE;
    if (!abs_path_part) return ESP_ERR_INVALID_ARG;

    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.f = fopen(abs_path_part, "wb");
    if (!s_ctx.f) {
        ESP_LOGE(TAG, "fopen %s fallita", abs_path_part);
        return ESP_FAIL;
    }
    uint8_t hdr[WAV_HEADER_SIZE];
    wav_header_build(hdr, 0);
    if (fwrite(hdr, 1, sizeof(hdr), s_ctx.f) != sizeof(hdr)) {
        fclose(s_ctx.f); s_ctx.f = NULL;
        return ESP_FAIL;
    }
    s_ctx.done = xSemaphoreCreateBinary();
    if (!s_ctx.done) { fclose(s_ctx.f); s_ctx.f = NULL; return ESP_ERR_NO_MEM; }
    s_ctx.err = ESP_OK;
    s_ctx.running = true;
    s_ctx.start_us = esp_timer_get_time();
    if (xTaskCreate(capture_task, "capture", CAPTURE_TASK_STACK, &s_ctx, CAPTURE_TASK_PRIO, NULL) != pdPASS) {
        s_ctx.running = false;
        vSemaphoreDelete(s_ctx.done); s_ctx.done = NULL;
        fclose(s_ctx.f); s_ctx.f = NULL;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "registrazione avviata: %s", abs_path_part);
    return ESP_OK;
}

esp_err_t capture_stop(capture_result_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));
    if (!s_ctx.f) return ESP_ERR_INVALID_STATE;

    s_ctx.stop_req = true;
    if (s_ctx.running && xSemaphoreTake(s_ctx.done, pdMS_TO_TICKS(CAPTURE_STOP_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "il task registratore non si e' fermato in %d ms", CAPTURE_STOP_WAIT_MS);
        if (s_ctx.err == ESP_OK) s_ctx.err = ESP_ERR_TIMEOUT;
    }

    esp_err_t err = s_ctx.err;
    fflush(s_ctx.f);
    fsync(fileno(s_ctx.f));
    uint8_t hdr[WAV_HEADER_SIZE];
    wav_header_build(hdr, s_ctx.data_bytes);
    if (fseek(s_ctx.f, 0, SEEK_SET) != 0 || fwrite(hdr, 1, sizeof(hdr), s_ctx.f) != sizeof(hdr)) {
        ESP_LOGE(TAG, "patch header WAV fallita");
        if (err == ESP_OK) err = ESP_FAIL;
    }
    fflush(s_ctx.f);
    fsync(fileno(s_ctx.f));
    fclose(s_ctx.f);
    s_ctx.f = NULL;
    if (s_ctx.done) { vSemaphoreDelete(s_ctx.done); s_ctx.done = NULL; }

    out->data_bytes = s_ctx.data_bytes;
    out->duration_ms = wav_bytes_to_ms(s_ctx.data_bytes);
    out->hit_max = s_ctx.hit_max;
    out->err = err;
    ESP_LOGI(TAG, "registrazione fermata: %lu byte, %lu ms, max=%d, err=%s",
             (unsigned long)out->data_bytes, (unsigned long)out->duration_ms, out->hit_max, esp_err_to_name(err));
    return err;
}

bool capture_is_running(void) { return s_ctx.running; }

uint32_t capture_elapsed_ms(void)
{
    if (!s_ctx.f) return 0;
    return (uint32_t)((esp_timer_get_time() - s_ctx.start_us) / 1000);
}
