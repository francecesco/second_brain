#pragma once
// Costanti di prodotto del firmware Second Brain (spec Fase 1a §3, §5, §6).
// Tutti i numeri "di comportamento" stanno qui: i moduli non hanno numeri magici.

// Fuso orario SOLO per il display (RTC, ora di sistema e nomi file sono UTC).
#define SB_TZ                      "CET-1CEST,M3.5.0,M10.5.0/3"

// Cattura (§5)
#define SB_CAPTURE_MAX_MS          (10 * 60 * 1000)   // 10 min -> ~19.2 MB
#define SB_CAPTURE_MIN_MS          1000               // sotto: scartata
#define SB_SD_MIN_FREE_BYTES       (25ULL * 1024 * 1024)

// Beep di feedback (altoparlante esterno): frequenza e durate
#define SB_BEEP_FREQ_HZ            1000
#define SB_BEEP_START_MS           120   // uno, all'avvio della registrazione (prima del primo campione)
#define SB_BEEP_STOP_MS            200   // uno, piu' lungo, dopo il salvataggio

// Taratura della tensione (2026-10-11, senza multimetro): il massimo letto durante la carica,
// 4091 mV grezzi, sono i 4,20 V di fine carica del caricabatterie. Da rifare col multimetro.
#define SB_BATTERY_MV_SCALE_PERMILLE 1027  // tensione corretta = grezza * 1,027

// Tasti a click (spec tasti §3): campionamento, antirimbalzo e finestra del triplo click.
#define SB_BUTTON_SAMPLE_MS        10
#define SB_BUTTON_DEBOUNCE_SAMPLES 3      // 30 ms stabili per cambiare stato
#define SB_DEV_CLICK_WINDOW_MS     2000   // dall'avvio dell'app: un click in piu' qui = DEV
                                          // (l'app parte ~0,5 s dopo il click che sveglia)

// Sorgente di alimentazione (stima: la scheda non segnala la USB). Soglie in mV corretti.
#define SB_USB_VOLTAGE_MV          4230  // sopra si considera USB: una batteria vera non supera 4,20 V,
                                         // senza batteria si legge 4,25-4,29 V (4,14-4,18 grezzi)
#define SB_BATTERY_MIN_RUNNING_MV  3300  // sotto, a batteria il device non sarebbe acceso
#define SB_BATTERY_MAX_SPREAD_MV   150   // dispersione massima di campioni ravvicinati di una batteria vera
#define SB_BATTERY_SAMPLES         8     // campioni per lettura, a 5 ms l'uno
#define SB_CHARGE_RISE_MV          30    // salita tra due cicli che indica carica in corso
#define SB_CHARGE_TREND_MAX_AGE_S  3600  // la lettura precedente vale solo se piu' recente di cosi'
#define SB_BATTERY_FULL_MV         4150  // su USB con batteria: da qui "piena" (fine carica a 4,2 V,
                                         // poi si assesta a ~4,17; la fase finale a tensione costante
                                         // conta gia' come piena)

// Batteria (§3)
#define SB_BATTERY_MIN_RECORD_PCT  10   // sotto: niente registrazione, niente Wi-Fi
#define SB_BATTERY_MIN_OTA_PCT     30   // sotto: niente OTA pull
#define SB_BATTERY_DISPLAY_STEP_PCT 2   // a un wake da timer la percentuale ridisegna lo schermo

// Schermata Oggi: immagine 1-bit scaricata dal backend (/digest/today), in cache su SD
#define SB_DIGEST_W          200
#define SB_DIGEST_H          136
#define SB_DIGEST_Y          30
#define SB_DIGEST_BYTES      (SB_DIGEST_W * SB_DIGEST_H / 8)   // 3400
#define SB_DIGEST_ETAG_MAX   48
#define SB_DIGEST_FILE       "digest.bin"
#define SB_DIGEST_ETAG_FILE  "digest.etag"
#define SB_DIGEST_TIMEOUT_MS 8000
                                        // solo se si sposta di tanto (le letture oscillano di 1)

// Rete e sync (§6.3)
#define SB_WIFI_BUDGET_MS          12000  // dopo la scansione; su AP affollati 8 s non bastano
#define SB_HTTP_IDLE_TIMEOUT_MS    15000  // inattivita' socket, non totale
#define SB_OTA_MANIFEST_TIMEOUT_MS 10000  // GET del manifest (pochi byte)
#define SB_OTA_DOWNLOAD_TIMEOUT_MS 15000  // inattivita' socket durante il download del binario
#define SB_SYNC_WINDOW_MS          (3 * 60 * 1000)
// Wake periodico (spec wake periodico §3): il device si risveglia da solo per sync, OTA
// e display, con o senza file in coda. Da rivedere con le misure di consumo a batteria.
#define SB_WAKE_PERIOD_MS          (5 * 60 * 1000)

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
