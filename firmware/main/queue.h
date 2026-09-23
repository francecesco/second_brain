#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

// Coda delle catture = directory STORAGE_MOUNT/queue (spec §6.1):
//   <nome>.wav       cattura in attesa di upload
//   <nome>.wav.part  cattura in corso (o interrotta: vedi queue_recover)
//   rejected/        catture rifiutate dal server con 4xx
#define QUEUE_PATH_MAX 128

esp_err_t queue_init(void);           // mkdir + queue_recover; richiede SD montata
int       queue_recover(void);        // .part -> .wav se >= 1 s, altrimenti cancellati; ritorna i promossi

// Nuova cattura: sceglie il nome (RTC valido -> cap_YYYYMMDD_HHMMSS, altrimenti
// cap_unsynced_NNNNNN da NVS), gestisce le collisioni (_2, _3...), ritorna il path
// assoluto del .wav.part da passare a capture_start e l'id (nome senza estensione).
esp_err_t queue_new_part_path(char *out_path, size_t n, char *out_id, size_t id_n);
esp_err_t queue_commit(const char *part_path);    // rename .wav.part -> .wav
esp_err_t queue_discard(const char *part_path);   // unlink

int       queue_count(uint64_t *out_total_bytes); // numero di .wav (e byte totali, opzionale)
int       queue_list(char names[][QUEUE_PATH_MAX], int max); // nomi file .wav, ordine alfabetico
esp_err_t queue_delete(const char *name);         // unlink queue/<name>
esp_err_t queue_reject(const char *name);         // rename in queue/rejected/<name>
void      queue_abs_path(const char *name, char *out, size_t n);
