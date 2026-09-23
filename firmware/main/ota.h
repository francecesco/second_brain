#pragma once
#include "esp_err.h"

// Scarica il manifest OTA da manifest_url; se la versione annunciata è più
// nuova di fw_version(), scarica il .bin, verifica lo sha256 contro quello
// dichiarato nel manifest, scrive la partizione OTA inattiva e riavvia.
// Ritorna ESP_OK se non c'è nulla da fare (nessun aggiornamento) o se il
// riavvio non è stato raggiunto per un errore già loggato; non ritorna mai
// dopo un aggiornamento riuscito (esp_restart()).
esp_err_t ota_pull(const char *manifest_url);

// Solo in dev mode: avvia un server HTTP (porta 80) con l'endpoint
// `POST /ota` che riceve un .bin in streaming dal Mac, lo scrive nella
// partizione OTA inattiva, la imposta come boot e riavvia (~1s dopo aver
// risposto 200). Nessun confronto di versione: in dev si flasha quel che
// arriva. Uso: curl --data-binary @build/secondbrain_fw.bin http://<ip>/ota
esp_err_t ota_dev_server_start(void);

// Da chiamare presto nel boot, appena il "boot base" e' ok (display su) e
// comunque PRIMA di qualunque deep sleep: se l'app in esecuzione e' appena
// stata installata via OTA (stato ESP_OTA_IMG_PENDING_VERIFY) la conferma
// con esp_ota_mark_app_valid_cancel_rollback(). Se invece l'app va in crash
// o si riavvia (anche per wake da deep sleep) senza aver confermato, il
// bootloader la marca ABORTED e ripristina la partizione precedente.
void ota_mark_valid_if_pending(void);
