#pragma once
#include "esp_err.h"
#include <time.h>

// Inizializza i sensori di bordo: assicura il bus I2C condiviso (SHTC3 + RTC
// PCF85063, lo stesso bus usato dal codec audio ES8311 via driver legacy
// driver/i2c.h) e configura l'ADC oneshot per la lettura della batteria
// (BOARD_BAT_ADC_CHAN) (partitore sempre collegato).
esp_err_t sensors_init(void);

// Legge temperatura (°C) e umidita' relativa (%) dal sensore SHTC3.
esp_err_t sensors_read_climate(float *temp_c, float *humidity);

// Legge data/ora corrente dall'RTC PCF85063 (registri BCD) in `struct tm`
// (tm_year da 1900, tm_mon 0-11, come da convenzione POSIX).
esp_err_t sensors_read_time(struct tm *out);

// Legge la tensione di batteria (volt, dopo il partitore BOARD_BAT_DIVIDER) e
// una percentuale approssimativa di carica LiPo (3.3V=0%, 4.2V=100%, clamp).
esp_err_t sensors_read_battery(float *volts, int *percent);

// Scrive data/ora (UTC) nel PCF85063. Richiede sensors_init().
esp_err_t sensors_set_time(const struct tm *utc);
