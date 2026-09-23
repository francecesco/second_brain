#pragma once
#include "esp_err.h"
// GET /status in DEV mode (spec §8): JSON con versione, batteria, ora, coda.
esp_err_t status_http_register(void);
