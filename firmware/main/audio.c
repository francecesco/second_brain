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
#include "board_i2c.h"
#include "driver/i2s_std.h"
#include "es8311.h"

static const char *TAG = "audio";

#define AUDIO_AMP_WARMUP_MS 200   // l'amplificatore ha bisogno di tempo per svegliarsi: con 5 ms il beep non si sentiva
#define AUDIO_BEEP_TAIL_MS  150   // silenzio in coda al tono (> ring DMA TX da ~90 ms)
#define AUDIO_BEEP_VOLUME   70    // reg32 = 178 (~ -6.5 dB); 55 era ~ -26 dB, inudibile in ufficio    // 0-100, volume DAC del beep
#define AUDIO_MCLK_MULTIPLE 256   // MCLK = 256 * Fs (sufficiente per PCM 16-bit, vedi nota nell'esempio IDF)

static i2s_chan_handle_t s_rx_handle = NULL;
static bool s_amp_on = false;
static es8311_handle_t s_es8311 = NULL;
static bool s_inited = false;

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
    gpio_set_level(BOARD_AUDIO_PA_EN, BOARD_AUDIO_PA_OFF); // amplificatore OFF: si accende solo per il beep
    vTaskDelay(pdMS_TO_TICKS(10));        // assestamento alimentazione prima di parlare via I2C

    ESP_LOGI(TAG, "power: AUDIO_PWR(GPIO%d)=ON, PA_EN(GPIO%d)=OFF", BOARD_AUDIO_PWR, BOARD_AUDIO_PA_EN);
    return ESP_OK;
}

static esp_err_t audio_i2c_init(void)
{
    return board_i2c_ensure(); // bus condiviso con i sensori: chi arriva primo installa
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
    // Uscita DAC muta: si apre solo dentro audio_beep().
    es8311_voice_mute(s_es8311, true);

    ESP_LOGI(TAG, "ES8311 pronto: %d Hz mono 16-bit, i2c addr 0x%02x, mclk %d Hz",
             AUDIO_SAMPLE_RATE, BOARD_ES8311_ADDR, AUDIO_SAMPLE_RATE * AUDIO_MCLK_MULTIPLE);
    return ESP_OK;
}

// Configurazione std comune (16 kHz, 16 bit, mono, slot sinistro). Il codec riceve MCLK/
// BCLK/WS dal canale attivo, che sia RX (registrazione) o TX (beep).
static i2s_std_config_t audio_std_cfg(int dout, int din)
{
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK,
            .bclk = BOARD_I2S_BCLK,
            .ws = BOARD_I2S_WS,
            .dout = dout,
            .din = din,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.clk_cfg.mclk_multiple = AUDIO_MCLK_MULTIPLE;
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    return std_cfg;
}

// Canale RX (solo ingresso): e' il percorso di registrazione collaudato in Fase 0/1a.
// NON si usa il full-duplex: con TX e RX allocati insieme il microfono finiva sullo slot
// sbagliato (livello -20 dB, verificato il 2026-09-24).
static esp_err_t audio_rx_open(void)
{
    if (s_rx_handle) return ESP_OK;
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    // Buffer DMA di ~1 s (16 x 1000 frame a 16 kHz): il default (~90 ms) andava in overflow
    // durante le scritture lente della SD e la registrazione perdeva secondi (misurato:
    // 8.6 s di tempo reale -> 6.7 s nel file).
    chan_cfg.dma_desc_num = 16;
    chan_cfg.dma_frame_num = 1000;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_rx_handle), TAG, "i2s_new_channel rx");
    i2s_std_config_t std_cfg = audio_std_cfg(I2S_GPIO_UNUSED, BOARD_I2S_DIN);
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx_handle, &std_cfg), TAG, "i2s init rx");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx_handle), TAG, "i2s enable rx");
    return ESP_OK;
}

static void audio_rx_close(void)
{
    if (!s_rx_handle) return;
    i2s_channel_disable(s_rx_handle);
    i2s_del_channel(s_rx_handle);
    s_rx_handle = NULL;
}

static esp_err_t audio_i2s_init(void)
{
    ESP_RETURN_ON_ERROR(audio_rx_open(), TAG, "rx open");
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

esp_err_t audio_read_block(int16_t *buf, size_t bytes, size_t *out_bytes, uint32_t timeout_ms)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;
    if (!buf || !out_bytes || bytes == 0) return ESP_ERR_INVALID_ARG;
    *out_bytes = 0;
    return i2s_channel_read(s_rx_handle, buf, bytes, out_bytes, pdMS_TO_TICKS(timeout_ms));
}

esp_err_t audio_beep(int freq_hz, int duration_ms)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;
    if (freq_hz <= 0 || duration_ms <= 0) return ESP_ERR_INVALID_ARG;

    // Tono + coda di silenzio: il DMA TX ha ~90 ms di zeri gia' in coda all'avvio, e senza
    // la coda un tono corto veniva tagliato quando si spegneva l'amplificatore.
    const int tone = AUDIO_SAMPLE_RATE * duration_ms / 1000;
    const int tail = AUDIO_SAMPLE_RATE * AUDIO_BEEP_TAIL_MS / 1000;
    const int n = tone + tail;
    int16_t *buf = calloc((size_t)n, sizeof(int16_t));
    if (!buf) return ESP_ERR_NO_MEM;
    const int ramp = AUDIO_SAMPLE_RATE * 5 / 1000; // 5 ms di rampa per evitare i click
    for (int i = 0; i < tone; i++) {
        float env = 1.0f;
        if (i < ramp) env = (float)i / ramp;
        else if (i > tone - ramp) env = (float)(tone - i) / ramp;
        buf[i] = (int16_t)(env * 12000.0f * sinf(2.0f * 3.14159265f * freq_hz * i / AUDIO_SAMPLE_RATE));
    }

    // Il beep suona solo quando NON si registra: si chiude il canale RX, si apre un
    // canale TX temporaneo (stesso clock verso il codec), si suona, si torna a RX.
    audio_rx_close();
    i2s_chan_handle_t tx = NULL;
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true; // in underrun manda zeri, mai il loop dell'ultimo blocco
    esp_err_t err = i2s_new_channel(&chan_cfg, &tx, NULL);
    if (err == ESP_OK) {
        i2s_std_config_t std_cfg = audio_std_cfg(BOARD_I2S_DOUT, I2S_GPIO_UNUSED);
        err = i2s_channel_init_std_mode(tx, &std_cfg);
        if (err == ESP_OK) err = i2s_channel_enable(tx);
    }
    ESP_LOGI(TAG, "beep %d Hz %d ms: tx=%s", freq_hz, duration_ms, esp_err_to_name(err));
    if (err == ESP_OK) {
        es8311_voice_volume_set(s_es8311, AUDIO_BEEP_VOLUME, NULL);
        es8311_voice_mute(s_es8311, false);
        bool warm = s_amp_on;
        if (!warm) { gpio_set_level(BOARD_AUDIO_PA_EN, BOARD_AUDIO_PA_ON); vTaskDelay(pdMS_TO_TICKS(AUDIO_AMP_WARMUP_MS)); }
        size_t written = 0;
        i2s_channel_write(tx, buf, (size_t)n * sizeof(int16_t), &written, pdMS_TO_TICKS(duration_ms + 200));
        vTaskDelay(pdMS_TO_TICKS(AUDIO_BEEP_TAIL_MS)); // il write ritorna quando i dati sono nel DMA: la coda assicura che il tono sia uscito
        if (!warm) gpio_set_level(BOARD_AUDIO_PA_EN, BOARD_AUDIO_PA_OFF);
        es8311_voice_mute(s_es8311, true);
    } else {
        ESP_LOGW(TAG, "beep: canale TX non disponibile (%s)", esp_err_to_name(err));
    }
    if (tx) { i2s_channel_disable(tx); i2s_del_channel(tx); }
    free(buf);
    esp_err_t rx_err = audio_rx_open();
    if (rx_err != ESP_OK) ESP_LOGE(TAG, "beep: riapertura RX fallita: %s", esp_err_to_name(rx_err));
    return err != ESP_OK ? err : rx_err;
}

void audio_amp_enable(bool on)
{
    gpio_set_level(BOARD_AUDIO_PA_EN, on ? BOARD_AUDIO_PA_ON : BOARD_AUDIO_PA_OFF);
    s_amp_on = on;
}
