#pragma once
#include "esp_err.h"

// Scarica il manifest OTA da manifest_url; se la versione annunciata è più
// nuova di fw_version(), scarica il .bin, verifica lo sha256 contro quello
// dichiarato nel manifest, scrive la partizione OTA inattiva e riavvia.
// Ritorna ESP_OK se non c'è nulla da fare (nessun aggiornamento) o se il
// riavvio non è stato raggiunto per un errore già loggato; non ritorna mai
// dopo un aggiornamento riuscito (esp_restart()).
esp_err_t ota_pull(const char *manifest_url);
