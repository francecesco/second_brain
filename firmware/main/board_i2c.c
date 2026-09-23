#include "board_i2c.h"
#include "board.h"
#include "driver/i2c.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "i2c";
static bool s_ready = false;

esp_err_t board_i2c_ensure(void)
{
    if (s_ready) return ESP_OK;
    // Ramo di alimentazione audio/periferiche (BOARD_AUDIO_PWR, attivo basso): in Fase 0
    // veniva acceso da audio_init prima di ogni lettura I2C e l'RTC non ha mai fallito;
    // con il ramo spento le letture del PCF85063 vanno in timeout a intermittenza.
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << BOARD_AUDIO_PWR) | (1ULL << BOARD_AUDIO_PA_EN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    gpio_set_level(BOARD_AUDIO_PWR, 0);   // ON
    gpio_set_level(BOARD_AUDIO_PA_EN, 1); // amplificatore OFF
    vTaskDelay(pdMS_TO_TICKS(10));
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = BOARD_I2C_SDA,
        .scl_io_num = BOARD_I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 100000,
    };
    esp_err_t err = i2c_param_config(BOARD_I2C_PORT, &conf);
    if (err != ESP_OK) { ESP_LOGE(TAG, "i2c_param_config: %s", esp_err_to_name(err)); return err; }
    err = i2c_driver_install(BOARD_I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "i2c_driver_install: %s", esp_err_to_name(err));
        return err;
    }
    vTaskDelay(pdMS_TO_TICKS(10)); // assestamento bus/periferiche prima della prima transazione
    s_ready = true;
    ESP_LOGI(TAG, "I2C pronto: porto=%d sda=%d scl=%d", BOARD_I2C_PORT, BOARD_I2C_SDA, BOARD_I2C_SCL);
    return ESP_OK;
}
