// Codec ES8311 (I2C) + I2S RX standard -> registrazione WAV su microSD.
//
// Riferimenti (clone di lavoro non incluso nel repo, vedi board.h):
//   github.com/waveshareteam/ESP32-S3-ePaper-1.54, 02_Example/ESP-IDF/V2/08_Audio_Test/
//   - components/codec_board/board_cfg.txt, voce "S3_ePaper_1_54": conferma
//     in_out ES8311, i2c {sda:47,scl:48}, i2s {mclk:14,bclk:15,ws:38,dout:45,din:16},
//     pa: 46, use_mclk: 1.
//   - components/board_power_bsp/board_power_bsp.cpp + components/user_app/user_app.cpp:
//     POWEER_Audio_ON() (GPIO Audio_PWR, attivo basso) va asserito PRIMA di i2c/codec.
// Componente driver ES8311 (I2C-only, l'utente gestisce l'I2S): pacchetto registry
// "espressif/es8311" (vedi idf_component.yml), stessa API usata dall'esempio ufficiale
// ESP-IDF v5.3.1 examples/peripherals/i2s/i2s_codec/i2s_es8311/main/i2s_es8311_example.c
// (es8311_create/es8311_init/es8311_microphone_config + driver/i2s_std.h in modalita' std).
//
// Nota MCLK: con AUDIO_MCLK_MULTIPLE=256 e AUDIO_SAMPLE_RATE=16000, MCLK=4.096MHz,
// combinazione presente nella tabella coefficienti del driver ES8311
// (managed_components/espressif__es8311/es8311.c, {4096000, 16000, ...}).

#include "audio.h"
#include "board.h"
#include "storage.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/i2s_std.h"
#include "es8311.h"

static const char *TAG = "audio";

#define AUDIO_I2C_CLK_HZ    100000
#define AUDIO_MCLK_MULTIPLE 256   // MCLK = 256 * Fs (sufficiente per PCM 16-bit, vedi nota nell'esempio IDF)
#define AUDIO_CHUNK_SAMPLES 2048  // campioni (int16) per blocco di lettura I2S -> 4KB, allocati sull'heap
                                  // (MAI come array locale: il task "main" ha uno stack piccolo, ~3.5KB,
                                  // e un buffer di queste dimensioni in stack lo fa andare in overflow)

static i2s_chan_handle_t s_rx_handle = NULL;
static es8311_handle_t s_es8311 = NULL;
static bool s_inited = false;

// Header WAV canonico, 44 byte, PCM.
typedef struct __attribute__((packed)) {
    char     riff_id[4];
    uint32_t riff_size;
    char     wave_id[4];
    char     fmt_id[4];
    uint32_t fmt_size;
    uint16_t audio_format;
    uint16_t num_channels;
    uint32_t sample_rate;
    uint32_t byte_rate;
    uint16_t block_align;
    uint16_t bits_per_sample;
    char     data_id[4];
    uint32_t data_size;
} wav_header_t;

_Static_assert(sizeof(wav_header_t) == 44, "header WAV deve essere 44 byte");

static void wav_header_fill(wav_header_t *h, uint32_t data_bytes)
{
    memcpy(h->riff_id, "RIFF", 4);
    memcpy(h->wave_id, "WAVE", 4);
    memcpy(h->fmt_id, "fmt ", 4);
    memcpy(h->data_id, "data", 4);
    h->fmt_size = 16;
    h->audio_format = 1; // PCM
    h->num_channels = 1;
    h->sample_rate = AUDIO_SAMPLE_RATE;
    h->bits_per_sample = 16;
    h->block_align = (uint16_t)(h->num_channels * h->bits_per_sample / 8);
    h->byte_rate = h->sample_rate * h->block_align;
    h->data_size = data_bytes;
    h->riff_size = 36 + data_bytes;
}

// Assicura BOARD_AUDIO_PWR (ramo alimentazione audio) e BOARD_AUDIO_PA_EN
// (amplificatore speaker) in uno stato noto. Entrambi attivo-basso.
// La registrazione usa solo il percorso microfono: PA_EN resta spento (1=OFF).
static esp_err_t audio_power_enable(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << BOARD_AUDIO_PWR) | (1ULL << BOARD_AUDIO_PA_EN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&io), TAG, "gpio_config power-enable");

    gpio_set_level(BOARD_AUDIO_PWR, 0);   // ON: alimenta il codec
    gpio_set_level(BOARD_AUDIO_PA_EN, 1); // OFF: amplificatore speaker non serve per registrare
    vTaskDelay(pdMS_TO_TICKS(10));        // assestamento alimentazione prima di parlare via I2C

    ESP_LOGI(TAG, "power: AUDIO_PWR(GPIO%d)=ON, PA_EN(GPIO%d)=OFF", BOARD_AUDIO_PWR, BOARD_AUDIO_PA_EN);
    return ESP_OK;
}

static esp_err_t audio_i2c_init(void)
{
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = BOARD_I2C_SDA,
        .scl_io_num = BOARD_I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = AUDIO_I2C_CLK_HZ,
    };
    ESP_RETURN_ON_ERROR(i2c_param_config(BOARD_I2C_PORT, &conf), TAG, "i2c_param_config");

    esp_err_t err = i2c_driver_install(BOARD_I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) { // ERR_INVALID_STATE = gia' installato altrove
        ESP_LOGE(TAG, "i2c_driver_install fallita: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "I2C pronto: porto=%d sda=%d scl=%d", BOARD_I2C_PORT, BOARD_I2C_SDA, BOARD_I2C_SCL);
    return ESP_OK;
}

static esp_err_t audio_codec_init(void)
{
    s_es8311 = es8311_create(BOARD_I2C_PORT, BOARD_ES8311_ADDR);
    if (s_es8311 == NULL) {
        ESP_LOGE(TAG, "es8311_create fallita: nessuna risposta I2C da 0x%02x (indirizzo non verificato su questa scheda)",
                 BOARD_ES8311_ADDR);
        return ESP_FAIL;
    }

    const es8311_clock_config_t clk_cfg = {
        .mclk_inverted = false,
        .sclk_inverted = false,
        .mclk_from_mclk_pin = true, // board_cfg.txt: use_mclk = 1
        .mclk_frequency = AUDIO_SAMPLE_RATE * AUDIO_MCLK_MULTIPLE,
        .sample_frequency = AUDIO_SAMPLE_RATE,
    };
    ESP_RETURN_ON_ERROR(es8311_init(s_es8311, &clk_cfg, ES8311_RESOLUTION_16, ES8311_RESOLUTION_16),
                         TAG, "es8311_init");
    ESP_RETURN_ON_ERROR(es8311_microphone_config(s_es8311, false /* mic analogico */),
                         TAG, "es8311_microphone_config");
    ESP_RETURN_ON_ERROR(es8311_microphone_gain_set(s_es8311, ES8311_MIC_GAIN_24DB),
                         TAG, "es8311_microphone_gain_set");

    ESP_LOGI(TAG, "ES8311 pronto: %d Hz mono 16-bit, i2c addr 0x%02x, mclk %d Hz",
             AUDIO_SAMPLE_RATE, BOARD_ES8311_ADDR, AUDIO_SAMPLE_RATE * AUDIO_MCLK_MULTIPLE);
    return ESP_OK;
}

static esp_err_t audio_i2s_init(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_rx_handle), TAG, "i2s_new_channel");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK,
            .bclk = BOARD_I2S_BCLK,
            .ws = BOARD_I2S_WS,
            .dout = I2S_GPIO_UNUSED, // solo RX: nessun canale TX creato
            .din = BOARD_I2S_DIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    std_cfg.clk_cfg.mclk_multiple = AUDIO_MCLK_MULTIPLE;
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT; // cattura deterministica del canale mono

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx_handle, &std_cfg), TAG, "i2s_channel_init_std_mode");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx_handle), TAG, "i2s_channel_enable");

    ESP_LOGI(TAG, "I2S RX std pronto: mclk=%d bclk=%d ws=%d din=%d",
             BOARD_I2S_MCLK, BOARD_I2S_BCLK, BOARD_I2S_WS, BOARD_I2S_DIN);
    return ESP_OK;
}

esp_err_t audio_init(void)
{
    if (s_inited) {
        ESP_LOGI(TAG, "audio_init: gia' inizializzato");
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(audio_power_enable(), TAG, "power enable");
    ESP_RETURN_ON_ERROR(audio_i2c_init(), TAG, "i2c init");
    ESP_RETURN_ON_ERROR(audio_codec_init(), TAG, "codec init");
    ESP_RETURN_ON_ERROR(audio_i2s_init(), TAG, "i2s init");

    s_inited = true;
    ESP_LOGI(TAG, "audio_init completato");
    return ESP_OK;
}

esp_err_t audio_record_wav(const char *relpath, int seconds)
{
    if (!s_inited) {
        ESP_LOGE(TAG, "audio_record_wav: audio_init non chiamato");
        return ESP_ERR_INVALID_STATE;
    }
    if (relpath == NULL || seconds <= 0) {
        return ESP_ERR_INVALID_ARG;
    }

    char path[300];
    int n = snprintf(path, sizeof(path), "%s/%s", STORAGE_MOUNT, relpath);
    if (n < 0 || (size_t)n >= sizeof(path)) {
        return ESP_ERR_INVALID_ARG;
    }

    const uint32_t byte_rate = AUDIO_SAMPLE_RATE * sizeof(int16_t); // mono, 16-bit
    const uint32_t data_bytes = byte_rate * (uint32_t)seconds;

    FILE *f = fopen(path, "wb");
    if (f == NULL) {
        ESP_LOGE(TAG, "impossibile aprire %s in scrittura", path);
        return ESP_FAIL;
    }

    wav_header_t header;
    wav_header_fill(&header, data_bytes);
    if (fwrite(&header, 1, sizeof(header), f) != sizeof(header)) {
        ESP_LOGE(TAG, "scrittura header WAV fallita su %s", path);
        fclose(f);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "registrazione avviata: %s, %d s attesi, %lu byte PCM attesi",
             path, seconds, (unsigned long)data_bytes);

    // Buffer di lettura I2S sull'HEAP (non un array locale): un chunk da
    // AUDIO_CHUNK_SAMPLES campioni sullo stack del task "main" (~3.5KB)
    // provoca uno stack overflow ("A stack overflow in task main has been
    // detected"). Si legge/scrive a blocchi piccoli finche' non si raggiungono
    // i byte richiesti per `seconds`.
    int16_t *chunk = malloc(AUDIO_CHUNK_SAMPLES * sizeof(int16_t));
    if (chunk == NULL) {
        ESP_LOGE(TAG, "malloc buffer chunk fallita (%u byte)",
                 (unsigned)(AUDIO_CHUNK_SAMPLES * sizeof(int16_t)));
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    uint32_t bytes_written_total = 0;
    esp_err_t ret = ESP_OK;

    // Livello audio complessivo (picco massimo e RMS globale su tutta la
    // registrazione), riassunto in una singola riga di log a fine funzione:
    // la diagnostica per-chunk (usata per il bring-up hardware) e' stata
    // rimossa ora che il mic/lo slot I2S sono verificati su hardware reale.
    int overall_peak = 0;
    int64_t overall_sum_sq = 0;
    uint32_t overall_samples = 0;

    while (bytes_written_total < data_bytes) {
        size_t to_read = AUDIO_CHUNK_SAMPLES * sizeof(int16_t);
        if (data_bytes - bytes_written_total < to_read) {
            to_read = data_bytes - bytes_written_total;
        }

        size_t bytes_read = 0;
        esp_err_t err = i2s_channel_read(s_rx_handle, chunk, to_read, &bytes_read, pdMS_TO_TICKS(1000));
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "i2s_channel_read fallita: %s", esp_err_to_name(err));
            ret = err;
            break;
        }
        if (bytes_read == 0) {
            continue;
        }

        size_t samples = bytes_read / sizeof(int16_t);
        for (size_t s = 0; s < samples; s++) {
            int v = chunk[s];
            int av = (v < 0) ? -v : v;
            if (av > overall_peak) {
                overall_peak = av;
            }
            overall_sum_sq += (int64_t)v * (int64_t)v;
        }
        overall_samples += (uint32_t)samples;

        size_t written = fwrite(chunk, 1, bytes_read, f);
        if (written != bytes_read) {
            ESP_LOGE(TAG, "scrittura PCM incompleta su %s", path);
            ret = ESP_FAIL;
            break;
        }
        bytes_written_total += (uint32_t)written;
    }

    free(chunk);

    // Ripatcha SEMPRE l'header con i byte realmente scritti, anche in caso di
    // errore/interruzione: cosi' anche una registrazione troncata lascia sulla
    // SD un WAV valido (RIFF/data size coerenti con il PCM effettivamente
    // presente) invece di dichiarare la durata nominale con meno dati reali.
    wav_header_fill(&header, bytes_written_total);
    if (fseek(f, 0, SEEK_SET) != 0) {
        ESP_LOGE(TAG, "fseek su %s fallita: impossibile ripatchare l'header WAV", path);
        if (ret == ESP_OK) {
            ret = ESP_FAIL;
        }
    } else if (fwrite(&header, 1, sizeof(header), f) != sizeof(header)) {
        ESP_LOGE(TAG, "riscrittura header WAV su %s fallita", path);
        if (ret == ESP_OK) {
            ret = ESP_FAIL;
        }
    }

    fclose(f);

    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "registrazione fallita dopo %lu byte su %s", (unsigned long)bytes_written_total, path);
        return ret;
    }

    int overall_rms = (overall_samples > 0)
                           ? (int)sqrt((double)overall_sum_sq / (double)overall_samples)
                           : 0;
    ESP_LOGI(TAG, "registrazione completata: %s (%lu byte PCM, peak max=%d, rms medio=%d)",
             path, (unsigned long)bytes_written_total, overall_peak, overall_rms);
    return ESP_OK;
}
