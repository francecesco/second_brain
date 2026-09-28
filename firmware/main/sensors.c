// Sensori di bordo: climatico SHTC3 (I2C), RTC PCF85063 (I2C) e tensione
// batteria (ADC oneshot + partitore).
//
// Riferimenti (clone di lavoro non incluso nel repo, vedi board.h):
//   github.com/waveshareteam/ESP32-S3-ePaper-1.54, 02_Example/ESP-IDF/V2/
//   - 03_I2C_SHTC3/components/i2c_equipment/i2c_equipment.h: comandi SHTC3
//     (WAKEUP 0x3517, MEAS_T_RH_POLLING 0x7866 = T prima, senza clock
//     stretching, SLEEP 0xB098) e formula di conversione raw -> °C/%RH.
//   - 02_I2C_PCF85063/components/SensorLib/src/{REG/PCF85063Constants.h,
//     SensorPCF85063.hpp}: mappa registri (0x04 SEC .. 0x0A YEAR, BCD,
//     letti in un colpo solo a partire da 0x04) e maschere bit (SEC/MIN
//     0x7F, HR 0x3F in modalita' 24h, DAY 0x3F, WEEKDAY 0x07, MONTH 0x1F,
//     YEAR + 2000).
//   - 01_ADC_Test/components/adc_bsp/adc_bsp.c: ADC_UNIT_1 canale
//     ADC_CHANNEL_3, ADC_ATTEN_DB_12, 12 bit, calibrazione curve-fitting,
//     volt = (mV letti / 1000) * 2 (partitore 2:1, BOARD_BAT_DIVIDER).
//
// NOTA bus condiviso: SHTC3 e PCF85063 stanno sullo STESSO bus I2C legacy
// (driver/i2c.h) gia' inizializzato da audio_init() (Task 6) per il codec
// ES8311, sulla stessa porta BOARD_I2C_PORT, e audio_init() gira SEMPRE
// PRIMA dei sensori in app_main. NON si usa il nuovo driver i2c_master
// (conflligerebbe con il driver legacy sulla stessa porta), e sensors_init
// NON richiama ne' i2c_param_config ne' i2c_driver_install: su questa scheda
// una i2c_driver_install ridondante sullo stesso NUM porta ritorna ESP_FAIL
// (non ESP_ERR_INVALID_STATE come atteso in precedenza), quindi qualsiasi
// tentativo di reinstallare/riconfigurare il bus va evitato del tutto. Il
// bus e' posseduto e configurato da audio_init(); sensors.c si limita a
// usarlo per le proprie transazioni I2C.

#include "sensors.h"
#include "board.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "board_i2c.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"

static const char *TAG = "sensors";

#define SENSORS_I2C_TIMEOUT_MS  1000

static bool s_i2c_ready = false;
static adc_oneshot_unit_handle_t s_adc_handle = NULL;
static adc_cali_handle_t s_adc_cali = NULL;
static bool s_adc_cali_ok = false;

// ---------------------------------------------------------------------------
// I2C: bus condiviso (legacy driver/i2c.h), posseduto/configurato da audio_init()
// ---------------------------------------------------------------------------

// Non installa/configura nulla: il bus BOARD_I2C_PORT e' gia' pronto per l'uso
// perche' audio_init() (Task 6) chiama i2c_param_config()+i2c_driver_install()
// PRIMA che sensors_init() venga invocata in app_main. Una i2c_driver_install
// ridondante su questa porta ha dimostrato di fallire con ESP_FAIL (non
// ESP_ERR_INVALID_STATE) su hardware reale, quindi qui ci si limita a
// verificare che il bus sia gia' stato inizializzato da qualcun altro
// (nessuna chiamata al driver): sensors_init() marca solo lo stato locale
// pronto per le transazioni I2C dei sensori.
static esp_err_t sensors_i2c_bus_init(void)
{
    // Bus condiviso con il codec audio: board_i2c_ensure() e' idempotente, chi arriva
    // primo installa il driver (su questa scheda una seconda i2c_driver_install fallisce).
    return board_i2c_ensure();
}

static esp_err_t i2c_write(uint8_t addr, const uint8_t *data, size_t len)
{
    return i2c_master_write_to_device(BOARD_I2C_PORT, addr, data, len, pdMS_TO_TICKS(SENSORS_I2C_TIMEOUT_MS));
}

static esp_err_t i2c_read(uint8_t addr, uint8_t *data, size_t len)
{
    return i2c_master_read_from_device(BOARD_I2C_PORT, addr, data, len, pdMS_TO_TICKS(SENSORS_I2C_TIMEOUT_MS));
}

static esp_err_t i2c_write_read_reg(uint8_t addr, uint8_t reg, uint8_t *data, size_t len)
{
    return i2c_master_write_read_device(BOARD_I2C_PORT, addr, &reg, 1, data, len, pdMS_TO_TICKS(SENSORS_I2C_TIMEOUT_MS));
}

// ---------------------------------------------------------------------------
// Batteria: ADC oneshot su BAT_ADC (partitore 2:1 su VBAT)
// ---------------------------------------------------------------------------

static esp_err_t sensors_battery_init(void)
{
    // Il partitore di BAT_ADC e' sempre collegato. GPIO17 NON va toccato qui: e' il
    // mantenimento dell'alimentazione da batteria (BOARD_BAT_LATCH, gestito da power.c).
    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&init_cfg, &s_adc_handle), TAG, "adc_oneshot_new_unit");

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc_handle, BOARD_BAT_ADC_CHAN, &chan_cfg),
                         TAG, "adc_oneshot_config_channel");

    adc_cali_curve_fitting_config_t cali_cfg = {
        .unit_id = ADC_UNIT_1,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    esp_err_t cali_err = adc_cali_create_scheme_curve_fitting(&cali_cfg, &s_adc_cali);
    s_adc_cali_ok = (cali_err == ESP_OK);
    if (!s_adc_cali_ok) {
        ESP_LOGW(TAG, "calibrazione ADC non disponibile (%s): uso conversione lineare di fallback",
                 esp_err_to_name(cali_err));
    }

    ESP_LOGI(TAG, "ADC batteria pronto: canale %d", BOARD_BAT_ADC_CHAN);
    return ESP_OK;
}

// ---------------------------------------------------------------------------
// sensors_init
// ---------------------------------------------------------------------------

esp_err_t sensors_init(void)
{
    // Idempotente: GET /status la richiama a ogni richiesta, e una seconda
    // adc_oneshot_new_unit sullo stesso ADC fallirebbe.
    static bool s_done = false;
    if (s_done) return ESP_OK;
    ESP_RETURN_ON_ERROR(sensors_i2c_bus_init(), TAG, "i2c init");
    s_i2c_ready = true;

    ESP_RETURN_ON_ERROR(sensors_battery_init(), TAG, "battery init");

    s_done = true;
    ESP_LOGI(TAG, "sensors_init completato");
    return ESP_OK;
}

// ---------------------------------------------------------------------------
// SHTC3: temperatura + umidita'
// ---------------------------------------------------------------------------

// CRC-8 Sensirion (poly 0x31, init 0xFF), usato dal SHTC3 per ogni parola a 16 bit.
static uint8_t shtc3_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0xFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

esp_err_t sensors_read_climate(float *temp_c, float *humidity)
{
    if (!s_i2c_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    if (temp_c == NULL || humidity == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    static const uint8_t cmd_wakeup[2]  = {0x35, 0x17}; // WAKEUP
    static const uint8_t cmd_measure[2] = {0x78, 0x66}; // MEAS_T_RH_POLLING (T prima, no clock stretch)
    static const uint8_t cmd_sleep[2]   = {0xB0, 0x98}; // SLEEP

    ESP_RETURN_ON_ERROR(i2c_write(BOARD_SHTC3_ADDR, cmd_wakeup, sizeof(cmd_wakeup)), TAG, "shtc3 wakeup");
    vTaskDelay(pdMS_TO_TICKS(1)); // datasheet: tWAKE-UP max 240us

    ESP_RETURN_ON_ERROR(i2c_write(BOARD_SHTC3_ADDR, cmd_measure, sizeof(cmd_measure)), TAG, "shtc3 measure");
    vTaskDelay(pdMS_TO_TICKS(20)); // datasheet: tMEAS max ~12.1ms in modalita' normale, margine incluso

    uint8_t raw[6];
    esp_err_t err = i2c_read(BOARD_SHTC3_ADDR, raw, sizeof(raw));
    // best-effort: rimette il sensore a dormire indipendentemente dall'esito della lettura
    i2c_write(BOARD_SHTC3_ADDR, cmd_sleep, sizeof(cmd_sleep));
    ESP_RETURN_ON_ERROR(err, TAG, "shtc3 read");

    if (shtc3_crc8(&raw[0], 2) != raw[2]) {
        ESP_LOGW(TAG, "SHTC3: CRC temperatura non valido");
    }
    if (shtc3_crc8(&raw[3], 2) != raw[5]) {
        ESP_LOGW(TAG, "SHTC3: CRC umidita' non valido");
    }

    uint16_t raw_t = ((uint16_t)raw[0] << 8) | raw[1];
    uint16_t raw_h = ((uint16_t)raw[3] << 8) | raw[4];

    *temp_c   = -45.0f + 175.0f * ((float)raw_t / 65536.0f);
    *humidity = 100.0f * ((float)raw_h / 65536.0f);

    return ESP_OK;
}

// ---------------------------------------------------------------------------
// PCF85063: data/ora
// ---------------------------------------------------------------------------

static inline uint8_t bcd2dec(uint8_t v)
{
    return (uint8_t)(((v >> 4) * 10) + (v & 0x0F));
}

esp_err_t sensors_read_time(struct tm *out)
{
    if (!s_i2c_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    // Lettura in un colpo solo a partire da SEC_REG (0x04): SEC, MIN, HR, DAY, WEEKDAY, MONTH, YEAR.
    uint8_t buf[7];
    esp_err_t err = i2c_write_read_reg(BOARD_PCF85063_ADDR, 0x04, buf, sizeof(buf));
    if (err != ESP_OK) { // la prima transazione dopo l'init del bus a volte va in timeout: un retry
        vTaskDelay(pdMS_TO_TICKS(20));
        err = i2c_write_read_reg(BOARD_PCF85063_ADDR, 0x04, buf, sizeof(buf));
    }
    ESP_RETURN_ON_ERROR(err, TAG, "pcf85063 read");

    memset(out, 0, sizeof(*out));
    out->tm_sec  = bcd2dec(buf[0] & 0x7F); // bit7 = OS (oscillator stop), ignorato
    out->tm_min  = bcd2dec(buf[1] & 0x7F);
    out->tm_hour = bcd2dec(buf[2] & 0x3F); // modalita' 24h
    out->tm_mday = bcd2dec(buf[3] & 0x3F);
    out->tm_wday = bcd2dec(buf[4] & 0x07);
    out->tm_mon  = bcd2dec(buf[5] & 0x1F) - 1; // struct tm: 0-11
    out->tm_year = bcd2dec(buf[6]) + 2000 - 1900; // struct tm: anni da 1900

    return ESP_OK;
}

static uint8_t dec2bcd(int v) { return (uint8_t)(((v / 10) << 4) | (v % 10)); }

esp_err_t sensors_set_time(const struct tm *utc)
{
    if (!s_i2c_ready) return ESP_ERR_INVALID_STATE;
    if (!utc) return ESP_ERR_INVALID_ARG;
    uint8_t buf[8];
    buf[0] = 0x04; // SEC_REG: scrittura sequenziale SEC..YEAR
    buf[1] = dec2bcd(utc->tm_sec) & 0x7F;      // bit7 OS (oscillator stop) = 0
    buf[2] = dec2bcd(utc->tm_min);
    buf[3] = dec2bcd(utc->tm_hour);
    buf[4] = dec2bcd(utc->tm_mday);
    buf[5] = (uint8_t)(utc->tm_wday & 0x07);
    buf[6] = dec2bcd(utc->tm_mon + 1);
    buf[7] = dec2bcd((utc->tm_year + 1900) % 100);
    esp_err_t err = i2c_write(BOARD_PCF85063_ADDR, buf, sizeof(buf));
    if (err != ESP_OK) ESP_LOGE(TAG, "pcf85063 write: %s", esp_err_to_name(err));
    return err;
}

// ---------------------------------------------------------------------------
// Batteria: tensione + percentuale
// ---------------------------------------------------------------------------

esp_err_t sensors_read_battery(float *volts, int *percent)
{
    if (volts == NULL || percent == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_adc_handle == NULL) {
        return ESP_ERR_INVALID_STATE;
    }


    int raw = 0;
    esp_err_t err = adc_oneshot_read(s_adc_handle, BOARD_BAT_ADC_CHAN, &raw);

    int mv = 0;
    if (err == ESP_OK) {
        if (s_adc_cali_ok) {
            err = adc_cali_raw_to_voltage(s_adc_cali, raw, &mv);
        } else {
            // Fallback lineare: 12 bit, fondo scala ~3.3V con ADC_ATTEN_DB_12.
            mv = (int)(((float)raw / 4095.0f) * 3300.0f);
        }
    }

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "lettura ADC batteria fallita: %s", esp_err_to_name(err));
        return err;
    }

    float v = ((float)mv / 1000.0f) * BOARD_BAT_DIVIDER;
    *volts = v;

    float pct = (v - 3.3f) / (4.2f - 3.3f) * 100.0f;
    if (pct < 0.0f) {
        pct = 0.0f;
    } else if (pct > 100.0f) {
        pct = 100.0f;
    }
    *percent = (int)(pct + 0.5f);

    return ESP_OK;
}
