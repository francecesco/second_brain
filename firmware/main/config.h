#pragma once
// Costanti di prodotto del firmware Second Brain (spec Fase 1a §3, §5, §6).
// Tutti i numeri "di comportamento" stanno qui: i moduli non hanno numeri magici.

// Fuso orario SOLO per il display (RTC, ora di sistema e nomi file sono UTC).
#define SB_TZ                      "CET-1CEST,M3.5.0,M10.5.0/3"

// Cattura (§5)
#define SB_CAPTURE_MAX_MS          (10 * 60 * 1000)   // 10 min -> ~19.2 MB
#define SB_CAPTURE_MIN_MS          1000               // sotto: scartata
#define SB_SD_MIN_FREE_BYTES       (25ULL * 1024 * 1024)

// Batteria (§3)
#define SB_BATTERY_MIN_RECORD_PCT  10   // sotto: niente registrazione, niente Wi-Fi
#define SB_BATTERY_MIN_OTA_PCT     30   // sotto: niente OTA pull

// Rete e sync (§6.3)
#define SB_WIFI_BUDGET_MS          8000
#define SB_HTTP_IDLE_TIMEOUT_MS    15000  // inattivita' socket, non totale
#define SB_SYNC_WINDOW_MS          (3 * 60 * 1000)

// Scadenza di ciclo (§4): deep sleep forzato se il ciclo si blocca.
#define SB_CYCLE_DEADLINE_MS       (5 * 60 * 1000)

// Time sync (§6.4)
#define SB_NTP_SERVER              "pool.ntp.org"
#define SB_NTP_WAIT_MS             5000
#define SB_NTP_MAX_AGE_S           (24 * 60 * 60)
#define SB_RTC_MIN_VALID_YEAR      2024

// Coda su SD (§6.1), percorsi relativi a STORAGE_MOUNT
#define SB_QUEUE_DIR               "queue"
#define SB_QUEUE_REJECTED_DIR      "queue/rejected"

// NVS (§10)
#define SB_NVS_NAMESPACE           "sb"
#define SB_NVS_KEY_CAPSEQ          "capseq"
#define SB_NVS_KEY_LASTNTP         "lastntp"
