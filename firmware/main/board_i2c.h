#pragma once
#include "esp_err.h"
// Bus I2C condiviso (SHTC3, RTC PCF85063, codec ES8311) su BOARD_I2C_PORT.
// Idempotente: il primo chiamante installa il driver, gli altri riusano.
esp_err_t board_i2c_ensure(void);
