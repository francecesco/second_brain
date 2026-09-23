# Firmware Fase 1a — Cattura record & forward + sync — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sostituire il ciclo di collaudo della Fase 0 con il firmware d'uso quotidiano: registrazione a durata libera tenendo PWR, coda su microSD che non perde nulla, upload verso `POST /captures` con ack, RTC sincronizzato via NTP, deep sleep tra un ciclo e l'altro.

**Architecture:** Macchina a stati sequenziale in `app_main` (boot base → modalità → CAPTURE → SYNC → display stato → deep sleep). Unica concorrenza: un task FreeRTOS registratore che scrive I2S→file mentre il main aggiorna e-Paper e campiona il tasto. La coda è la directory `queue/` sulla SD (un `.wav` = una cattura in attesa, `.wav.part` = in corso/interrotta). La logica decisionale (header WAV, nomi file, esito HTTP → azione, recupero `.part`, policy NTP) è in funzioni pure testate su host con unity (target `linux`), come `ota_manifest` e `wifi_bssid` in Fase 0.

**Tech Stack:** ESP-IDF v5.3.1 (esp32s3), FreeRTOS, FATFS via VFS (SDMMC 1-bit), `driver/i2s_std` + `espressif/es8311`, `esp_http_client`, `esp_http_server`, `esp_netif_sntp`, NVS, cJSON, unity (host). Python 3 stdlib per il server di test.

**Spec:** `docs/specs/2026-09-23-firmware-capture-sync-design.md` (leggerla prima: i numeri di sezione citati sotto, §x.y, sono quelli).

## Global Constraints

- Tutti i comandi `idf.py` richiedono prima `. ~/esp/esp-idf/export.sh`. Directory firmware: `firmware/` (target esp32s3), test host: `firmware/host_test/` (target linux, già configurato).
- Test host: `cd firmware/host_test && idf.py build && ./build/host_test.elf`. L'eseguibile stampa i risultati unity e **non termina da solo**: leggere l'output e chiuderlo con Ctrl-C (o `timeout 10 ./build/host_test.elf`). Tutti i test esistenti (9) devono restare verdi.
- Build firmware: `cd firmware && idf.py build`. Zero warning nuovi.
- Hardware: porta `/dev/cu.usbmodem1101`; in deep sleep la porta **sparisce**. Per flashare il device deve essere sveglio: DEV mode (fino al Task 4: PWR tenuto ~1 s al wake; dal Task 4 in poi: USER premuto entro 1 s dal wake con PWR). Flash: `idf.py -p /dev/cu.usbmodem1101 flash`. Monitor: `idf.py -p /dev/cu.usbmodem1101 monitor` (Ctrl-] per uscire) oppure lettura raw con pyserial.
- Push OTA senza cavo (device in DEV mode, sul router principale): `curl --data-binary @build/secondbrain_fw.bin http://<ip>/ota`.
- `main/secrets.h` è gitignored: mai committarlo. `secrets.h.example` sì.
- `version.txt` in git resta `0.1.0`; per i test OTA si bumpa localmente e si ripristina.
- Nessun `ESP_ERROR_CHECK` nel flusso normale di `app_main` tranne `display_init` e `power_init` (§9). I moduli ritornano `esp_err_t`.
- Buffer ≥ 1 KB sempre sull'heap, mai array locali (stack del main 10 KB, lezione Fase 0).
- Commit piccoli, messaggi in italiano con prefisso `firmware:` o `docs:`, **nessun riferimento all'assistente** (regola utente), autore `francecesco <francecesco78@gmail.com>` (config locale del repo).
- Tutte le costanti di prodotto (durate, soglie, budget) vivono in `main/config.h` (Task 1): niente numeri magici nei moduli.
- Costanti WAV: 16 000 Hz, mono, 16 bit → 32 000 byte/s; header 44 byte.

---

## File Structure

| File | Responsabilità | Task |
|---|---|---|
| `firmware/main/config.h` (new) | costanti di prodotto (SB_*), TZ | 1 |
| `firmware/sdkconfig.defaults` (mod) | FATFS long file names su heap | 1 |
| `firmware/main/wav.c/.h` (new, puro) | header WAV build/parse, byte↔ms | 1 |
| `firmware/main/capture_name.c/.h` (new, puro) | nome cattura da `struct tm` / contatore | 2 |
| `firmware/main/queue_policy.c/.h` (new, puro) | decisione su `.part` trovati al boot | 3 |
| `firmware/main/sync_policy.c/.h` (new, puro) | codice HTTP → azione | 3 |
| `firmware/main/timesync_policy.c/.h` (new, puro) | `timesync_needed`, `tm→epoch` UTC | 3 |
| `firmware/host_test/main/test_*.c` (new) | test unity per i moduli puri | 1–3 |
| `firmware/main/power.c/.h` (mod) | boot mode a 3 stati, `power_pwr_pressed` pubblico, scadenza di ciclo | 4 |
| `firmware/main/audio.c/.h` (mod) | `audio_read_block`; rimosso `audio_record_wav` | 5 |
| `firmware/main/capture.c/.h` (new) | task registratore, `.part` → file WAV valido | 5 |
| `firmware/main/storage.c/.h` (mod) | `storage_mounted`, `storage_free_bytes`, `storage_mkdir`; rimossi write/read di bring-up | 6 |
| `firmware/main/queue.c/.h` (new) | directory `queue/`: recover, list, next path, delete, reject | 6 |
| `firmware/main/display.c/.h` (mod) | `display_lines` a 3 righe | 7 |
| `firmware/main/app_main.c` (riscritto) | flusso §4 | 7 |
| `firmware/main/sensors.c/.h` (mod) | `sensors_set_time` (scrittura PCF85063) | 8 |
| `firmware/main/timesync.c/.h` (new) | RTC↔sistema, SNTP, NVS `lastntp` | 8 |
| `firmware/tools/capture_server.py` (new) | server di test `POST /captures` | 9 |
| `firmware/main/sync.c/.h` (new) | upload della coda con budget | 9 |
| `firmware/main/ota.c/.h` (mod) | `ota_dev_server_register` | 10 |
| `firmware/main/status_http.c/.h` (new) | `GET /status` JSON in DEV | 10 |
| `firmware/main/secrets.h.example`, `firmware/main/CMakeLists.txt`, `firmware/host_test/main/CMakeLists.txt` (mod) | registrazione sorgenti e nuova voce segreti | vari |

---

### Task 1: Fondamenta — `config.h`, nomi lunghi FAT, modulo `wav` (puro, host-test)

**Files:**
- Create: `firmware/main/config.h`
- Modify: `firmware/sdkconfig.defaults`
- Create: `firmware/main/wav.h`, `firmware/main/wav.c`
- Create: `firmware/host_test/main/test_wav.c`
- Modify: `firmware/host_test/main/CMakeLists.txt`, `firmware/host_test/main/test_ota_manifest.c` (registrazione RUN_TEST)
- Modify: `firmware/main/CMakeLists.txt`

**Interfaces:**
- Produces:
  - `config.h`: `SB_TZ`, `SB_CAPTURE_MAX_MS`, `SB_CAPTURE_MIN_MS`, `SB_BATTERY_MIN_RECORD_PCT`, `SB_BATTERY_MIN_OTA_PCT`, `SB_WIFI_BUDGET_MS`, `SB_HTTP_IDLE_TIMEOUT_MS`, `SB_SYNC_WINDOW_MS`, `SB_CYCLE_DEADLINE_MS`, `SB_SD_MIN_FREE_BYTES`, `SB_NTP_SERVER`, `SB_NTP_WAIT_MS`, `SB_NTP_MAX_AGE_S`, `SB_RTC_MIN_VALID_YEAR`, `SB_QUEUE_DIR`, `SB_QUEUE_REJECTED_DIR`, `SB_NVS_NAMESPACE`, `SB_NVS_KEY_CAPSEQ`, `SB_NVS_KEY_LASTNTP`
  - `wav.h`: `#define WAV_HEADER_SIZE 44`, `#define WAV_BYTES_PER_SEC 32000u`, `void wav_header_build(uint8_t out[WAV_HEADER_SIZE], uint32_t data_bytes)`, `bool wav_header_parse(const uint8_t in[WAV_HEADER_SIZE], uint32_t *data_bytes)`, `uint32_t wav_bytes_to_ms(uint32_t data_bytes)`

- [ ] **Step 1: `firmware/main/config.h`**

```c
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
```

- [ ] **Step 2: nomi lunghi FAT in `firmware/sdkconfig.defaults`** (append)

```
# Nomi file lunghi (cap_20260923_191530.wav > 8.3): LFN su heap.
CONFIG_FATFS_LFN_HEAP=y
CONFIG_FATFS_MAX_LFN=64
```

Poi, dato che `sdkconfig` esiste già ed è gitignored, applica il default anche al file
generato: `cd firmware && idf.py reconfigure` non basta per chiavi già presenti. Esegui:

```bash
cd firmware && sed -i '' -e 's/^CONFIG_FATFS_LFN_NONE=y/# CONFIG_FATFS_LFN_NONE is not set/' -e 's/^# CONFIG_FATFS_LFN_HEAP is not set/CONFIG_FATFS_LFN_HEAP=y/' sdkconfig && grep -n 'CONFIG_FATFS_LFN\|CONFIG_FATFS_MAX_LFN' sdkconfig
```

Expected: `CONFIG_FATFS_LFN_HEAP=y`, `CONFIG_FATFS_MAX_LFN=...` (se manca, aggiungi
`CONFIG_FATFS_MAX_LFN=64`). Se il sed non trova le righe, cancella `sdkconfig` e lascia
che `idf.py build` lo rigeneri dai defaults.

- [ ] **Step 3: test che fallisce — `firmware/host_test/main/test_wav.c`**

```c
#include "unity.h"
#include "wav.h"
#include <string.h>

void test_wav_header_build_layout(void) {
    uint8_t h[WAV_HEADER_SIZE];
    wav_header_build(h, 32000); // 1 s di audio
    TEST_ASSERT_EQUAL_MEMORY("RIFF", h, 4);
    TEST_ASSERT_EQUAL_MEMORY("WAVE", h + 8, 4);
    TEST_ASSERT_EQUAL_MEMORY("fmt ", h + 12, 4);
    TEST_ASSERT_EQUAL_MEMORY("data", h + 36, 4);
    // riff_size = 36 + data (little endian)
    uint32_t riff = h[4] | (h[5] << 8) | (h[6] << 16) | ((uint32_t)h[7] << 24);
    TEST_ASSERT_EQUAL_UINT32(36 + 32000, riff);
    // fmt: PCM=1, canali=1, sample rate 16000, byte rate 32000, block align 2, bits 16
    TEST_ASSERT_EQUAL_UINT16(1, h[20] | (h[21] << 8));
    TEST_ASSERT_EQUAL_UINT16(1, h[22] | (h[23] << 8));
    uint32_t sr = h[24] | (h[25] << 8) | (h[26] << 16) | ((uint32_t)h[27] << 24);
    TEST_ASSERT_EQUAL_UINT32(16000, sr);
    uint32_t br = h[28] | (h[29] << 8) | (h[30] << 16) | ((uint32_t)h[31] << 24);
    TEST_ASSERT_EQUAL_UINT32(32000, br);
    TEST_ASSERT_EQUAL_UINT16(2, h[32] | (h[33] << 8));
    TEST_ASSERT_EQUAL_UINT16(16, h[34] | (h[35] << 8));
    uint32_t data = h[40] | (h[41] << 8) | (h[42] << 16) | ((uint32_t)h[43] << 24);
    TEST_ASSERT_EQUAL_UINT32(32000, data);
}

void test_wav_header_parse_roundtrip(void) {
    uint8_t h[WAV_HEADER_SIZE];
    wav_header_build(h, 123456);
    uint32_t data = 0;
    TEST_ASSERT_TRUE(wav_header_parse(h, &data));
    TEST_ASSERT_EQUAL_UINT32(123456, data);
}

void test_wav_header_parse_rejects_garbage(void) {
    uint8_t h[WAV_HEADER_SIZE];
    memset(h, 0, sizeof(h));
    uint32_t data = 0;
    TEST_ASSERT_FALSE(wav_header_parse(h, &data));
    wav_header_build(h, 10);
    h[0] = 'X'; // RIFF rotto
    TEST_ASSERT_FALSE(wav_header_parse(h, &data));
}

void test_wav_bytes_to_ms(void) {
    TEST_ASSERT_EQUAL_UINT32(0, wav_bytes_to_ms(0));
    TEST_ASSERT_EQUAL_UINT32(1000, wav_bytes_to_ms(32000));
    TEST_ASSERT_EQUAL_UINT32(7031, wav_bytes_to_ms(225000)); // 225000/32 = 7031.25
}
```

Registra i test: in `firmware/host_test/main/test_ota_manifest.c`, prima di `app_main`
aggiungi le dichiarazioni e dentro `app_main` i `RUN_TEST`:

```c
void test_wav_header_build_layout(void);
void test_wav_header_parse_roundtrip(void);
void test_wav_header_parse_rejects_garbage(void);
void test_wav_bytes_to_ms(void);
```
```c
    RUN_TEST(test_wav_header_build_layout);
    RUN_TEST(test_wav_header_parse_roundtrip);
    RUN_TEST(test_wav_header_parse_rejects_garbage);
    RUN_TEST(test_wav_bytes_to_ms);
```

In `firmware/host_test/main/CMakeLists.txt` aggiungi a `SRCS`: `"test_wav.c" "../../main/wav.c"`.

- [ ] **Step 4: verifica che fallisca**

Run: `cd firmware/host_test && idf.py build 2>&1 | tail -5`
Expected: errore di compilazione (`wav.h: No such file` / `wav.c` mancante).

- [ ] **Step 5: `firmware/main/wav.h`**

```c
#pragma once
#include <stdbool.h>
#include <stdint.h>

// Header WAV canonico PCM 16 kHz mono 16 bit (44 byte). Funzioni pure, testate su host.
#define WAV_HEADER_SIZE   44
#define WAV_SAMPLE_RATE   16000u
#define WAV_BYTES_PER_SEC 32000u   // 16000 Hz * 1 canale * 2 byte

// Scrive in out i 44 byte dell'header per data_bytes byte di PCM.
void wav_header_build(uint8_t out[WAV_HEADER_SIZE], uint32_t data_bytes);

// Verifica RIFF/WAVE/fmt /data e ritorna la dimensione dichiarata del chunk data.
bool wav_header_parse(const uint8_t in[WAV_HEADER_SIZE], uint32_t *data_bytes);

// Durata in millisecondi di data_bytes byte di PCM a 16 kHz mono 16 bit.
uint32_t wav_bytes_to_ms(uint32_t data_bytes);
```

- [ ] **Step 6: `firmware/main/wav.c`**

```c
#include "wav.h"
#include <string.h>

static void put_u16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
static void put_u32(uint8_t *p, uint32_t v) { p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = v >> 24; }
static uint32_t get_u32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

void wav_header_build(uint8_t out[WAV_HEADER_SIZE], uint32_t data_bytes)
{
    memcpy(out + 0, "RIFF", 4);
    put_u32(out + 4, 36 + data_bytes);
    memcpy(out + 8, "WAVE", 4);
    memcpy(out + 12, "fmt ", 4);
    put_u32(out + 16, 16);              // fmt chunk size
    put_u16(out + 20, 1);               // PCM
    put_u16(out + 22, 1);               // mono
    put_u32(out + 24, WAV_SAMPLE_RATE);
    put_u32(out + 28, WAV_BYTES_PER_SEC);
    put_u16(out + 32, 2);               // block align
    put_u16(out + 34, 16);              // bits per sample
    memcpy(out + 36, "data", 4);
    put_u32(out + 40, data_bytes);
}

bool wav_header_parse(const uint8_t in[WAV_HEADER_SIZE], uint32_t *data_bytes)
{
    if (!in || !data_bytes) return false;
    if (memcmp(in + 0, "RIFF", 4) != 0) return false;
    if (memcmp(in + 8, "WAVE", 4) != 0) return false;
    if (memcmp(in + 12, "fmt ", 4) != 0) return false;
    if (memcmp(in + 36, "data", 4) != 0) return false;
    *data_bytes = get_u32(in + 40);
    return true;
}

uint32_t wav_bytes_to_ms(uint32_t data_bytes)
{
    return (uint32_t)(((uint64_t)data_bytes * 1000u) / WAV_BYTES_PER_SEC);
}
```

Aggiungi `"wav.c"` a `SRCS` in `firmware/main/CMakeLists.txt`.

- [ ] **Step 7: test verdi + build firmware**

Run: `cd firmware/host_test && idf.py build 2>&1 | grep -E 'error|warning' ; timeout 10 ./build/host_test.elf | grep -E 'PASS|FAIL|Tests'`
Expected: 13 test, `0 Failures`.
Run: `cd firmware && idf.py build 2>&1 | grep -E 'error|warning: |binary size'`
Expected: build ok, `CONFIG_FATFS_LFN_HEAP` attivo (nessun errore).

- [ ] **Step 8: Commit**

```bash
git add firmware/main/config.h firmware/sdkconfig.defaults firmware/main/wav.c firmware/main/wav.h firmware/main/CMakeLists.txt firmware/host_test/main/test_wav.c firmware/host_test/main/CMakeLists.txt firmware/host_test/main/test_ota_manifest.c
git commit -m "firmware: config.h con costanti di prodotto, nomi lunghi FAT e modulo wav con test host"
```

---

### Task 2: Nome cattura (puro, host-test)

**Files:**
- Create: `firmware/main/capture_name.h`, `firmware/main/capture_name.c`
- Create: `firmware/host_test/main/test_capture_name.c`
- Modify: `firmware/host_test/main/CMakeLists.txt`, `firmware/host_test/main/test_ota_manifest.c`, `firmware/main/CMakeLists.txt`

**Interfaces:**
- Consumes: `SB_RTC_MIN_VALID_YEAR` da `config.h`.
- Produces:
  - `#define CAPTURE_NAME_MAX 32`
  - `bool capture_name_rtc_valid(const struct tm *utc)` — anno ≥ 2024
  - `void capture_name_from_tm(const struct tm *utc, char *out, size_t n)` → `cap_YYYYMMDD_HHMMSS`
  - `void capture_name_unsynced(uint32_t seq, char *out, size_t n)` → `cap_unsynced_NNNNNN`
  - `void capture_name_with_suffix(const char *base, int k, char *out, size_t n)` → `base_k`

- [ ] **Step 1: test che fallisce — `firmware/host_test/main/test_capture_name.c`**

```c
#include "unity.h"
#include "capture_name.h"
#include <string.h>
#include <time.h>

static struct tm tm_at(int y, int mo, int d, int h, int mi, int s) {
    struct tm t; memset(&t, 0, sizeof(t));
    t.tm_year = y - 1900; t.tm_mon = mo - 1; t.tm_mday = d;
    t.tm_hour = h; t.tm_min = mi; t.tm_sec = s;
    return t;
}

void test_capture_name_from_tm(void) {
    struct tm t = tm_at(2026, 9, 23, 19, 15, 30);
    char out[CAPTURE_NAME_MAX];
    capture_name_from_tm(&t, out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("cap_20260923_191530", out);
}

void test_capture_name_unsynced(void) {
    char out[CAPTURE_NAME_MAX];
    capture_name_unsynced(42, out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("cap_unsynced_000042", out);
    capture_name_unsynced(1234567, out, sizeof(out)); // oltre 6 cifre: non tronca
    TEST_ASSERT_EQUAL_STRING("cap_unsynced_1234567", out);
}

void test_capture_name_rtc_valid(void) {
    struct tm bad = tm_at(2000, 1, 1, 0, 0, 0);
    struct tm ok = tm_at(2024, 1, 1, 0, 0, 0);
    TEST_ASSERT_FALSE(capture_name_rtc_valid(&bad));
    TEST_ASSERT_TRUE(capture_name_rtc_valid(&ok));
}

void test_capture_name_with_suffix(void) {
    char out[CAPTURE_NAME_MAX];
    capture_name_with_suffix("cap_20260923_191530", 2, out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("cap_20260923_191530_2", out);
}
```

Registra in `test_ota_manifest.c` (dichiarazioni + 4 `RUN_TEST`) e aggiungi a `SRCS` di
`host_test/main/CMakeLists.txt`: `"test_capture_name.c" "../../main/capture_name.c"`.

- [ ] **Step 2: verifica che fallisca**

Run: `cd firmware/host_test && idf.py build 2>&1 | tail -3`
Expected: errore `capture_name.h` mancante.

- [ ] **Step 3: `firmware/main/capture_name.h`**

```c
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

// Nomi delle catture (spec §5.3). Funzioni pure, testate su host.
// L'id cattura e' il nome SENZA estensione; il file e' "<nome>.wav".
#define CAPTURE_NAME_MAX 32

bool capture_name_rtc_valid(const struct tm *utc);                       // anno >= SB_RTC_MIN_VALID_YEAR
void capture_name_from_tm(const struct tm *utc, char *out, size_t n);    // cap_YYYYMMDD_HHMMSS (UTC)
void capture_name_unsynced(uint32_t seq, char *out, size_t n);           // cap_unsynced_NNNNNN
void capture_name_with_suffix(const char *base, int k, char *out, size_t n); // <base>_<k>
```

- [ ] **Step 4: `firmware/main/capture_name.c`**

```c
#include "capture_name.h"
#include "config.h"
#include <stdio.h>

bool capture_name_rtc_valid(const struct tm *utc)
{
    return utc && (utc->tm_year + 1900) >= SB_RTC_MIN_VALID_YEAR;
}

void capture_name_from_tm(const struct tm *utc, char *out, size_t n)
{
    snprintf(out, n, "cap_%04d%02d%02d_%02d%02d%02d",
             utc->tm_year + 1900, utc->tm_mon + 1, utc->tm_mday,
             utc->tm_hour, utc->tm_min, utc->tm_sec);
}

void capture_name_unsynced(uint32_t seq, char *out, size_t n)
{
    snprintf(out, n, "cap_unsynced_%06lu", (unsigned long)seq);
}

void capture_name_with_suffix(const char *base, int k, char *out, size_t n)
{
    snprintf(out, n, "%s_%d", base, k);
}
```

Aggiungi `"capture_name.c"` a `SRCS` in `firmware/main/CMakeLists.txt`.

- [ ] **Step 5: test verdi**

Run: `cd firmware/host_test && idf.py build 2>&1 | grep -E 'error|warning' ; timeout 10 ./build/host_test.elf | grep -E 'FAIL|Tests'`
Expected: `17 Tests 0 Failures`.

- [ ] **Step 6: Commit**

```bash
git add firmware/main/capture_name.c firmware/main/capture_name.h firmware/main/CMakeLists.txt firmware/host_test/main/test_capture_name.c firmware/host_test/main/CMakeLists.txt firmware/host_test/main/test_ota_manifest.c
git commit -m "firmware: nome cattura da RTC o contatore unsynced, con test host"
```

---

### Task 3: Policy pure — recupero `.part`, esito HTTP, time sync (host-test)

**Files:**
- Create: `firmware/main/queue_policy.h/.c`, `firmware/main/sync_policy.h/.c`, `firmware/main/timesync_policy.h/.c`
- Create: `firmware/host_test/main/test_policies.c`
- Modify: `firmware/host_test/main/CMakeLists.txt`, `firmware/host_test/main/test_ota_manifest.c`, `firmware/main/CMakeLists.txt`

**Interfaces:**
- Produces:
  - `typedef enum { QUEUE_PART_PROMOTE, QUEUE_PART_DROP } queue_part_decision_t; queue_part_decision_t queue_part_decide(uint64_t file_size_bytes);`
  - `typedef enum { SYNC_ACTION_DELETE, SYNC_ACTION_REJECT, SYNC_ACTION_STOP } sync_action_t; sync_action_t sync_decide(int http_status);`
  - `bool timesync_needed(int rtc_year, int64_t last_ntp_epoch, int64_t now_epoch);`
  - `int64_t timesync_tm_to_epoch_utc(const struct tm *utc);`

- [ ] **Step 1: test che fallisce — `firmware/host_test/main/test_policies.c`**

```c
#include "unity.h"
#include "queue_policy.h"
#include "sync_policy.h"
#include "timesync_policy.h"
#include <string.h>

void test_queue_part_decide(void) {
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_DROP, queue_part_decide(0));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_DROP, queue_part_decide(44));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_DROP, queue_part_decide(44 + 32000 - 1));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_PROMOTE, queue_part_decide(44 + 32000));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_PROMOTE, queue_part_decide(19000000));
}

void test_sync_decide(void) {
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(200));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(201));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(409));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(400));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(413));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(404));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(500));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(503));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(0));   // errore rete/timeout
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(-1));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(302)); // redirect: non gestito -> stop
}

void test_timesync_needed(void) {
    const int64_t now = 1800000000;                 // 2027-01-15 circa
    TEST_ASSERT_TRUE(timesync_needed(2000, now - 10, now));        // RTC non valido
    TEST_ASSERT_TRUE(timesync_needed(2026, 0, now));               // mai sincronizzato
    TEST_ASSERT_TRUE(timesync_needed(2026, now - 86401, now));     // piu' di 24 h
    TEST_ASSERT_FALSE(timesync_needed(2026, now - 3600, now));     // recente
    TEST_ASSERT_FALSE(timesync_needed(2026, now - 86400, now));    // esattamente 24 h: ok
}

void test_timesync_tm_to_epoch_utc(void) {
    struct tm t; memset(&t, 0, sizeof(t));
    t.tm_year = 1970 - 1900; t.tm_mon = 0; t.tm_mday = 1;
    TEST_ASSERT_EQUAL_INT64(0, timesync_tm_to_epoch_utc(&t));
    // 2026-09-23 19:15:30 UTC = 1790190930 (verificato: date -u -d @1790190930)
    t.tm_year = 2026 - 1900; t.tm_mon = 8; t.tm_mday = 23; t.tm_hour = 19; t.tm_min = 15; t.tm_sec = 30;
    TEST_ASSERT_EQUAL_INT64(1790190930, timesync_tm_to_epoch_utc(&t));
    // 2000-02-29 (bisestile) 00:00:00 = 951782400
    memset(&t, 0, sizeof(t)); t.tm_year = 100; t.tm_mon = 1; t.tm_mday = 29;
    TEST_ASSERT_EQUAL_INT64(951782400, timesync_tm_to_epoch_utc(&t));
}
```

Verifica i valori attesi sul Mac prima di fidarti: `python3 -c "import calendar;print(calendar.timegm((2026,9,23,19,15,30)), calendar.timegm((2000,2,29,0,0,0)))"` → `1790190930 951782400`. Se diverso, correggi il test con i valori di Python.

Registra in `test_ota_manifest.c` (dichiarazioni + 4 `RUN_TEST`) e aggiungi a `SRCS`:
`"test_policies.c" "../../main/queue_policy.c" "../../main/sync_policy.c" "../../main/timesync_policy.c"`.

- [ ] **Step 2: verifica che fallisca**

Run: `cd firmware/host_test && idf.py build 2>&1 | tail -3`
Expected: header mancanti.

- [ ] **Step 3: header e sorgenti**

`firmware/main/queue_policy.h`:
```c
#pragma once
#include <stdint.h>
// Decisione su un "<nome>.wav.part" trovato al boot (spec §5.5). Pura, testata su host.
typedef enum { QUEUE_PART_PROMOTE, QUEUE_PART_DROP } queue_part_decision_t;
queue_part_decision_t queue_part_decide(uint64_t file_size_bytes);
```
`firmware/main/queue_policy.c`:
```c
#include "queue_policy.h"
#include "wav.h"
#include "config.h"

queue_part_decision_t queue_part_decide(uint64_t file_size_bytes)
{
    // header + almeno SB_CAPTURE_MIN_MS di PCM
    const uint64_t min_bytes = WAV_HEADER_SIZE + ((uint64_t)WAV_BYTES_PER_SEC * SB_CAPTURE_MIN_MS) / 1000u;
    return file_size_bytes >= min_bytes ? QUEUE_PART_PROMOTE : QUEUE_PART_DROP;
}
```
`firmware/main/sync_policy.h`:
```c
#pragma once
// Codice HTTP della POST /captures -> azione sul file in coda (spec §6.2). Pura.
typedef enum {
    SYNC_ACTION_DELETE, // 200/201 accettata, 409 duplicato
    SYNC_ACTION_REJECT, // altri 4xx: sposta in rejected/, prosegui
    SYNC_ACTION_STOP,   // 5xx, redirect, errore rete (status <= 0): tieni, interrompi il sync
} sync_action_t;
sync_action_t sync_decide(int http_status);
```
`firmware/main/sync_policy.c`:
```c
#include "sync_policy.h"

sync_action_t sync_decide(int http_status)
{
    if (http_status == 200 || http_status == 201 || http_status == 409) return SYNC_ACTION_DELETE;
    if (http_status >= 400 && http_status < 500) return SYNC_ACTION_REJECT;
    return SYNC_ACTION_STOP;
}
```
`firmware/main/timesync_policy.h`:
```c
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <time.h>
// Policy di sincronizzazione ora (spec §6.4) e conversione tm(UTC)->epoch. Pure.
bool timesync_needed(int rtc_year, int64_t last_ntp_epoch, int64_t now_epoch);
int64_t timesync_tm_to_epoch_utc(const struct tm *utc);
```
`firmware/main/timesync_policy.c`:
```c
#include "timesync_policy.h"
#include "config.h"

bool timesync_needed(int rtc_year, int64_t last_ntp_epoch, int64_t now_epoch)
{
    if (rtc_year < SB_RTC_MIN_VALID_YEAR) return true;
    if (last_ntp_epoch <= 0) return true;
    return (now_epoch - last_ntp_epoch) > SB_NTP_MAX_AGE_S;
}

// Howard Hinnant, days_from_civil: giorni dal 1970-01-01 per una data del calendario
// gregoriano proleptico. Evita mktime/timegm, che dipendono da TZ e dalla libc.
static int64_t days_from_civil(int y, unsigned m, unsigned d)
{
    y -= m <= 2;
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = (unsigned)(y - era * 400);
    const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + (int64_t)doe - 719468;
}

int64_t timesync_tm_to_epoch_utc(const struct tm *utc)
{
    int64_t days = days_from_civil(utc->tm_year + 1900, (unsigned)(utc->tm_mon + 1), (unsigned)utc->tm_mday);
    return days * 86400 + utc->tm_hour * 3600 + utc->tm_min * 60 + utc->tm_sec;
}
```

Aggiungi a `SRCS` di `firmware/main/CMakeLists.txt`: `"queue_policy.c" "sync_policy.c" "timesync_policy.c"`.

- [ ] **Step 4: test verdi**

Run: `cd firmware/host_test && idf.py build 2>&1 | grep -E 'error|warning' ; timeout 10 ./build/host_test.elf | grep -E 'FAIL|Tests'`
Expected: `21 Tests 0 Failures`.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/queue_policy.* firmware/main/sync_policy.* firmware/main/timesync_policy.* firmware/main/CMakeLists.txt firmware/host_test/main/test_policies.c firmware/host_test/main/CMakeLists.txt firmware/host_test/main/test_ota_manifest.c
git commit -m "firmware: policy pure per recupero .part, esito HTTP e time sync, con test host"
```

---

### Task 4: Boot mode a 3 stati e scadenza di ciclo (`power.c`)

**Files:**
- Modify: `firmware/main/power.h`, `firmware/main/power.c`
- Modify: `firmware/main/app_main.c` (solo adattamento minimo all'enum: `BOOT_NORMAL` → `BOOT_SYNC_ONLY`)

**Interfaces:**
- Consumes: `SB_CYCLE_DEADLINE_MS` (config.h).
- Produces:
  - `typedef enum { BOOT_SYNC_ONLY, BOOT_CAPTURE, BOOT_DEV } boot_mode_t;`
  - `boot_mode_t power_boot_mode(void);` (campionamento §4.2)
  - `bool power_pwr_pressed(void);` (pubblico, era static) e `bool power_user_pressed(void);` (rinomina di `power_button_pressed`)
  - `void power_cycle_deadline_start(uint32_t ms);` (riavvia il timer se già attivo)
  - `void power_cycle_deadline_cancel(void);`
  - `const char *power_boot_mode_name(boot_mode_t m);`

- [ ] **Step 1: `firmware/main/power.h`** (sostituisci il contenuto)

```c
#pragma once
#include <stdbool.h>
#include <stdint.h>

// Modalita' di boot (spec Fase 1a §4.2), decisa campionando i tasti nel primo secondo:
//  - BOOT_DEV:       USER premuto entro 1 s -> Wi-Fi + server push/status, resta sveglio
//  - BOOT_CAPTURE:   PWR tenuto per tutti i 300 ms iniziali -> registra subito
//  - BOOT_SYNC_ONLY: pressione breve di PWR, oppure reset/power-on -> solo sync
typedef enum { BOOT_SYNC_ONLY, BOOT_CAPTURE, BOOT_DEV } boot_mode_t;

void power_init(void);
bool power_pwr_pressed(void);   // BOARD_BTN_PWR (GPIO18), wake source
bool power_user_pressed(void);  // BOARD_BTN_USER (GPIO0, strapping: mai come wake)
boot_mode_t power_boot_mode(void);
const char *power_boot_mode_name(boot_mode_t m);

// Scadenza di ciclo: dopo ms millisecondi forza power_deep_sleep() (spec §4.1).
// Chiamarla di nuovo riavvia il conteggio; cancel la disattiva (DEV mode).
void power_cycle_deadline_start(uint32_t ms);
void power_cycle_deadline_cancel(void);

// Deep sleep con wake EXT1 su BOARD_BTN_PWR. Non ritorna.
void power_deep_sleep(void);
```

- [ ] **Step 2: `firmware/main/power.c` — modifiche**

Rinomina `power_button_pressed` → `power_user_pressed`; rendi `power_pwr_pressed` non
static. Sostituisci `power_boot_mode` con:

```c
#include "esp_timer.h"
#include "config.h"

#define POWER_MODE_USER_WINDOW_US (1000 * 1000)  // finestra DEV per USER: 1 s dal boot

boot_mode_t power_boot_mode(void)
{
    // Fase 1: 6 campioni x 50 ms su entrambi i tasti.
    bool pwr_held_all = true;
    bool user_seen = false;
    for (int i = 0; i < POWER_PWR_DEBOUNCE_SAMPLES; i++) {
        if (power_user_pressed()) user_seen = true;
        if (!power_pwr_pressed()) pwr_held_all = false;
        vTaskDelay(pdMS_TO_TICKS(POWER_PWR_DEBOUNCE_STEP_MS));
    }
    if (user_seen) {
        ESP_LOGI(TAG, "boot-mode: USER premuto nei primi 300ms -> DEV");
        return BOOT_DEV;
    }
    if (pwr_held_all) {
        ESP_LOGI(TAG, "boot-mode: PWR tenuto su %d campioni -> CAPTURE", POWER_PWR_DEBOUNCE_SAMPLES);
        return BOOT_CAPTURE;
    }
    // Fase 2: PWR rilasciato (pressione breve o reset): finestra fino a 1 s per USER.
    while (esp_timer_get_time() < POWER_MODE_USER_WINDOW_US) {
        if (power_user_pressed()) {
            ESP_LOGI(TAG, "boot-mode: USER premuto entro 1s -> DEV");
            return BOOT_DEV;
        }
        vTaskDelay(pdMS_TO_TICKS(POWER_PWR_DEBOUNCE_STEP_MS));
    }
    ESP_LOGI(TAG, "boot-mode: nessun tasto tenuto -> SYNC_ONLY");
    return BOOT_SYNC_ONLY;
}

const char *power_boot_mode_name(boot_mode_t m)
{
    switch (m) {
    case BOOT_DEV:     return "DEV";
    case BOOT_CAPTURE: return "CAPTURE";
    default:           return "SYNC_ONLY";
    }
}

// --- scadenza di ciclo ------------------------------------------------------
static esp_timer_handle_t s_deadline = NULL;

static void deadline_cb(void *arg)
{
    (void)arg;
    ESP_LOGE(TAG, "scadenza di ciclo raggiunta: deep sleep forzato");
    power_deep_sleep();
}

void power_cycle_deadline_start(uint32_t ms)
{
    if (s_deadline == NULL) {
        const esp_timer_create_args_t args = { .callback = deadline_cb, .name = "cycle_deadline" };
        if (esp_timer_create(&args, &s_deadline) != ESP_OK) {
            ESP_LOGE(TAG, "esp_timer_create scadenza fallita");
            return;
        }
    }
    esp_timer_stop(s_deadline); // ok anche se non attivo
    esp_timer_start_once(s_deadline, (uint64_t)ms * 1000ULL);
    ESP_LOGI(TAG, "scadenza di ciclo: %lu ms", (unsigned long)ms);
}

void power_cycle_deadline_cancel(void)
{
    if (s_deadline) esp_timer_stop(s_deadline);
}
```

Nota: `esp_timer_get_time()` è in microsecondi dal boot dell'app (~0,4 s dopo il reset
per bootloader: la finestra "1 s dal boot" è misurata da qui, va bene).

In `app_main.c` (versione Fase 0, ancora in vita fino al Task 7): sostituisci
`BOOT_NORMAL` con `BOOT_SYNC_ONLY` e `mode == BOOT_DEV ? "DEV" : "NORMAL"` con
`power_boot_mode_name(mode)`. Aggiungi `#include "power.h"` già presente. Il ramo
"NORMAL" deve valere per **entrambi** `BOOT_SYNC_ONLY` e `BOOT_CAPTURE`: usa
`mode != BOOT_DEV`.

- [ ] **Step 3: build + verifica su hardware (HIL)**

Run: `cd firmware && idf.py build && idf.py -p /dev/cu.usbmodem1101 flash` (device sveglio in DEV mode Fase 0: PWR tenuto ~1 s al wake, PRIMA di flashare questo firmware).
Poi, con `idf.py -p /dev/cu.usbmodem1101 monitor` o pyserial, prova tre risvegli:
1. PWR tenuto 2 s → log `-> CAPTURE` (il resto del ciclo Fase 0 prosegue e dorme).
2. PWR breve → log `-> SYNC_ONLY`.
3. PWR premuto e rilasciato, poi USER entro 1 s → `-> DEV`, device resta sveglio con push server.
Expected: i tre log corrispondenti. **Da qui in avanti il DEV mode si ottiene con USER.**

- [ ] **Step 4: Commit**

```bash
git add firmware/main/power.c firmware/main/power.h firmware/main/app_main.c
git commit -m "firmware: boot mode a tre stati (DEV/CAPTURE/SYNC_ONLY) e scadenza di ciclo"
```

---

### Task 5: Registratore a durata libera (`audio_read_block` + `capture.c`)

**Files:**
- Modify: `firmware/main/audio.h`, `firmware/main/audio.c` (rimuovi `audio_record_wav` e `wav_header_*` locali; aggiungi `audio_read_block`)
- Create: `firmware/main/capture.h`, `firmware/main/capture.c`
- Modify: `firmware/main/CMakeLists.txt`, `firmware/main/app_main.c` (prova HIL temporanea)

**Interfaces:**
- Consumes: `wav.h` (Task 1), `SB_CAPTURE_MAX_MS` (config.h), `power_pwr_pressed()` (Task 4).
- Produces:
  - `esp_err_t audio_read_block(int16_t *buf, size_t bytes, size_t *out_bytes, uint32_t timeout_ms);`
  - `typedef struct { uint32_t data_bytes; uint32_t duration_ms; bool hit_max; esp_err_t err; } capture_result_t;`
  - `esp_err_t capture_start(const char *abs_path_part);` — apre il `.part`, scrive header vuoto, avvia il task
  - `esp_err_t capture_stop(capture_result_t *out);` — ferma, fsync, patch header, chiude (NON rinomina: lo fa `queue`)
  - `bool capture_is_running(void);`
  - `uint32_t capture_elapsed_ms(void);`

- [ ] **Step 1: `audio.h`** — sostituisci `audio_record_wav` con:

```c
// Legge fino a `bytes` byte di PCM 16-bit mono dal canale I2S RX in buf (allocato dal
// chiamante, sull'heap). Blocca al massimo timeout_ms. out_bytes = byte effettivi.
esp_err_t audio_read_block(int16_t *buf, size_t bytes, size_t *out_bytes, uint32_t timeout_ms);
```

In `audio.c`: elimina `wav_header_t`, `wav_header_fill`, `audio_record_wav` e `#include "storage.h"`, `<math.h>`; aggiungi:

```c
esp_err_t audio_read_block(int16_t *buf, size_t bytes, size_t *out_bytes, uint32_t timeout_ms)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;
    if (!buf || !out_bytes || bytes == 0) return ESP_ERR_INVALID_ARG;
    *out_bytes = 0;
    return i2s_channel_read(s_rx_handle, buf, bytes, out_bytes, pdMS_TO_TICKS(timeout_ms));
}
```

- [ ] **Step 2: `firmware/main/capture.h`**

```c
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

// Registratore a durata libera (spec §5.2): un task FreeRTOS legge l'I2S a blocchi da
// 128 ms e li appende al file; il chiamante decide quando fermare (rilascio tasto).
// Il file viene scritto come "<path>.wav.part" con header WAV provvisorio; capture_stop
// fa fsync, patcha l'header con i byte reali e chiude. Rinominare a .wav e' compito
// del chiamante (queue_commit), cosi' la decisione "scarta se < 1 s" resta fuori da qui.
typedef struct {
    uint32_t  data_bytes;   // PCM scritti
    uint32_t  duration_ms;  // = wav_bytes_to_ms(data_bytes)
    bool      hit_max;      // fermata dal limite SB_CAPTURE_MAX_MS
    esp_err_t err;          // ESP_OK oppure primo errore I2S/SD incontrato
} capture_result_t;

esp_err_t capture_start(const char *abs_path_part);  // richiede audio_init() e SD montata
esp_err_t capture_stop(capture_result_t *out);       // idempotente se non in corso
bool      capture_is_running(void);
uint32_t  capture_elapsed_ms(void);
```

- [ ] **Step 3: `firmware/main/capture.c`**

```c
#include "capture.h"
#include "audio.h"
#include "wav.h"
#include "config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "capture";

#define CAPTURE_BLOCK_BYTES   4096   // 128 ms a 32000 B/s
#define CAPTURE_TASK_STACK    4096
#define CAPTURE_TASK_PRIO     (tskIDLE_PRIORITY + 5)  // sopra il main (1)
#define CAPTURE_READ_TIMEOUT_MS 500
#define CAPTURE_STOP_WAIT_MS  3000

typedef struct {
    FILE *f;
    volatile bool stop_req;
    volatile bool running;
    volatile bool hit_max;
    volatile esp_err_t err;
    volatile uint32_t data_bytes;
    int64_t start_us;
    SemaphoreHandle_t done;
} capture_ctx_t;

static capture_ctx_t s_ctx = {0};

static void capture_task(void *arg)
{
    capture_ctx_t *c = (capture_ctx_t *)arg;
    const uint32_t max_bytes = (uint32_t)(((uint64_t)WAV_BYTES_PER_SEC * SB_CAPTURE_MAX_MS) / 1000u);
    int16_t *buf = malloc(CAPTURE_BLOCK_BYTES);
    if (!buf) {
        c->err = ESP_ERR_NO_MEM;
    } else {
        while (!c->stop_req) {
            if (c->data_bytes >= max_bytes) { c->hit_max = true; break; }
            size_t want = CAPTURE_BLOCK_BYTES;
            if (max_bytes - c->data_bytes < want) want = max_bytes - c->data_bytes;
            size_t got = 0;
            esp_err_t e = audio_read_block(buf, want, &got, CAPTURE_READ_TIMEOUT_MS);
            if (e != ESP_OK && e != ESP_ERR_TIMEOUT) { c->err = e; break; }
            if (got == 0) continue;
            if (fwrite(buf, 1, got, c->f) != got) {
                ESP_LOGE(TAG, "fwrite fallita a %lu byte", (unsigned long)c->data_bytes);
                c->err = ESP_FAIL;
                break;
            }
            c->data_bytes += (uint32_t)got;
        }
        free(buf);
    }
    c->running = false;
    xSemaphoreGive(c->done);
    vTaskDelete(NULL);
}

esp_err_t capture_start(const char *abs_path_part)
{
    if (s_ctx.running) return ESP_ERR_INVALID_STATE;
    if (!abs_path_part) return ESP_ERR_INVALID_ARG;

    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.f = fopen(abs_path_part, "wb");
    if (!s_ctx.f) {
        ESP_LOGE(TAG, "fopen %s fallita", abs_path_part);
        return ESP_FAIL;
    }
    uint8_t hdr[WAV_HEADER_SIZE];
    wav_header_build(hdr, 0);
    if (fwrite(hdr, 1, sizeof(hdr), s_ctx.f) != sizeof(hdr)) {
        fclose(s_ctx.f); s_ctx.f = NULL;
        return ESP_FAIL;
    }
    s_ctx.done = xSemaphoreCreateBinary();
    if (!s_ctx.done) { fclose(s_ctx.f); s_ctx.f = NULL; return ESP_ERR_NO_MEM; }
    s_ctx.err = ESP_OK;
    s_ctx.running = true;
    s_ctx.start_us = esp_timer_get_time();
    if (xTaskCreate(capture_task, "capture", CAPTURE_TASK_STACK, &s_ctx, CAPTURE_TASK_PRIO, NULL) != pdPASS) {
        s_ctx.running = false;
        vSemaphoreDelete(s_ctx.done); s_ctx.done = NULL;
        fclose(s_ctx.f); s_ctx.f = NULL;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "registrazione avviata: %s", abs_path_part);
    return ESP_OK;
}

esp_err_t capture_stop(capture_result_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));
    if (!s_ctx.f) return ESP_ERR_INVALID_STATE;

    s_ctx.stop_req = true;
    if (s_ctx.running && xSemaphoreTake(s_ctx.done, pdMS_TO_TICKS(CAPTURE_STOP_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "il task registratore non si e' fermato in %d ms", CAPTURE_STOP_WAIT_MS);
        if (s_ctx.err == ESP_OK) s_ctx.err = ESP_ERR_TIMEOUT;
    }

    esp_err_t err = s_ctx.err;
    fflush(s_ctx.f);
    fsync(fileno(s_ctx.f));
    uint8_t hdr[WAV_HEADER_SIZE];
    wav_header_build(hdr, s_ctx.data_bytes);
    if (fseek(s_ctx.f, 0, SEEK_SET) != 0 || fwrite(hdr, 1, sizeof(hdr), s_ctx.f) != sizeof(hdr)) {
        ESP_LOGE(TAG, "patch header WAV fallita");
        if (err == ESP_OK) err = ESP_FAIL;
    }
    fflush(s_ctx.f);
    fsync(fileno(s_ctx.f));
    fclose(s_ctx.f);
    s_ctx.f = NULL;
    if (s_ctx.done) { vSemaphoreDelete(s_ctx.done); s_ctx.done = NULL; }

    out->data_bytes = s_ctx.data_bytes;
    out->duration_ms = wav_bytes_to_ms(s_ctx.data_bytes);
    out->hit_max = s_ctx.hit_max;
    out->err = err;
    ESP_LOGI(TAG, "registrazione fermata: %lu byte, %lu ms, max=%d, err=%s",
             (unsigned long)out->data_bytes, (unsigned long)out->duration_ms, out->hit_max, esp_err_to_name(err));
    return err;
}

bool capture_is_running(void) { return s_ctx.running; }

uint32_t capture_elapsed_ms(void)
{
    if (!s_ctx.f) return 0;
    return (uint32_t)((esp_timer_get_time() - s_ctx.start_us) / 1000);
}
```

Aggiungi `"capture.c"` a `SRCS`.

- [ ] **Step 4: prova HIL temporanea in `app_main.c`** — sostituisci il blocco "recording 3s" con:

```c
    #include "capture.h"   // in testa al file
    ...
    ESP_ERROR_CHECK(audio_init());
    if (mode == BOOT_CAPTURE) {
        ESP_ERROR_CHECK(capture_start(STORAGE_MOUNT "/hold.wav.part"));
        display_text("REC", "tieni premuto");
        int released = 0;
        while (released < 2) {           // rilascio su 2 campioni consecutivi
            vTaskDelay(pdMS_TO_TICKS(50));
            released = power_pwr_pressed() ? 0 : released + 1;
            if (!capture_is_running()) break;
        }
        capture_result_t r;
        capture_stop(&r);
        ESP_LOGI(TAG, "hold-to-record: %lu ms", (unsigned long)r.duration_ms);
        rename(STORAGE_MOUNT "/hold.wav.part", STORAGE_MOUNT "/hold.wav");
    }
```

(rimuovi la chiamata a `audio_record_wav`, non esiste più; `#include <stdio.h>` per `rename`).

Run: `cd firmware && idf.py build && idf.py -p /dev/cu.usbmodem1101 flash` (device in DEV mode con USER).
Poi sveglia con PWR tenuto ~8 s parlando. Expected nel log: `registrazione avviata`, poi
`registrazione fermata: ... ~8000 ms` (tolleranza ±2 s per il refresh display, spec §13).
Estrai la SD (o leggila in DEV via SD reader): `hold.wav` riproducibile, header coerente
(`python3 -c "import wave;w=wave.open('hold.wav');print(w.getnframes()/w.getframerate())"`).
Se non hai un lettore SD: accetta il log come verifica e rimanda la riproduzione al Task 9
(il file arriva sul Mac via upload).

- [ ] **Step 5: Commit**

```bash
git add firmware/main/audio.c firmware/main/audio.h firmware/main/capture.c firmware/main/capture.h firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: registratore a durata libera su task dedicato, audio_read_block al posto di audio_record_wav"
```

---

### Task 6: Coda su SD (`storage` esteso + `queue.c`)

**Files:**
- Modify: `firmware/main/storage.h`, `firmware/main/storage.c` (rimuovi `storage_write/read`; aggiungi `storage_mounted`, `storage_free_bytes`, `storage_mkdir_p`)
- Create: `firmware/main/queue.h`, `firmware/main/queue.c`
- Modify: `firmware/main/CMakeLists.txt`, `firmware/main/app_main.c` (usa la coda nella prova HIL del Task 5)

**Interfaces:**
- Consumes: `capture_name.h`, `queue_policy.h`, `wav.h`, `config.h`, NVS.
- Produces:
  - `bool storage_mounted(void); esp_err_t storage_free_bytes(uint64_t *out); esp_err_t storage_mkdir_p(const char *relpath);`
  - `#define QUEUE_PATH_MAX 128`
  - `esp_err_t queue_init(void);` — crea `queue/` e `queue/rejected/`, poi `queue_recover()`
  - `int queue_recover(void);` — promuove/cancella i `.part`; ritorna quanti promossi
  - `esp_err_t queue_new_part_path(char *out, size_t n, char *id_out, size_t id_n);` — sceglie il nome (§5.3) e ritorna il path assoluto `.wav.part` e l'id
  - `esp_err_t queue_commit(const char *part_path);` — rename `.wav.part` → `.wav`
  - `esp_err_t queue_discard(const char *part_path);` — unlink
  - `int queue_count(uint64_t *total_bytes);`
  - `int queue_list(char names[][QUEUE_PATH_MAX], int max);` — nomi `*.wav` ordinati alfabeticamente
  - `esp_err_t queue_delete(const char *name); esp_err_t queue_reject(const char *name);`
  - `void queue_abs_path(const char *name, char *out, size_t n);`

- [ ] **Step 1: `storage.h`/`storage.c`**

`storage.h`:
```c
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define STORAGE_MOUNT "/sdcard"

esp_err_t storage_mount(void);                       // idempotente
bool      storage_mounted(void);
esp_err_t storage_free_bytes(uint64_t *out_free);    // spazio libero sul volume
esp_err_t storage_mkdir_p(const char *relpath);      // crea STORAGE_MOUNT/relpath (un livello alla volta)
```

In `storage.c` elimina `storage_write` e `storage_read`, aggiungi (con `#include <sys/stat.h>`,
`<errno.h>`, `<string.h>`):

```c
bool storage_mounted(void) { return s_card != NULL; }

esp_err_t storage_free_bytes(uint64_t *out_free)
{
    if (!s_card) return ESP_ERR_INVALID_STATE;
    if (!out_free) return ESP_ERR_INVALID_ARG;
    uint64_t total = 0, free_b = 0;
    esp_err_t err = esp_vfs_fat_info(STORAGE_MOUNT, &total, &free_b);
    if (err != ESP_OK) return err;
    *out_free = free_b;
    return ESP_OK;
}

esp_err_t storage_mkdir_p(const char *relpath)
{
    if (!s_card) return ESP_ERR_INVALID_STATE;
    char path[160];
    int n = snprintf(path, sizeof(path), "%s/%s", STORAGE_MOUNT, relpath);
    if (n < 0 || (size_t)n >= sizeof(path)) return ESP_ERR_INVALID_ARG;
    // crea ogni livello dopo il mount point
    for (char *p = path + strlen(STORAGE_MOUNT) + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (mkdir(path, 0775) != 0 && errno != EEXIST) return ESP_FAIL;
            *p = '/';
        }
    }
    if (mkdir(path, 0775) != 0 && errno != EEXIST) {
        ESP_LOGE(TAG, "mkdir %s fallita: errno %d", path, errno);
        return ESP_FAIL;
    }
    return ESP_OK;
}
```

- [ ] **Step 2: `firmware/main/queue.h`**

```c
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
```

- [ ] **Step 3: `firmware/main/queue.c`**

```c
#include "queue.h"
#include "storage.h"
#include "config.h"
#include "capture_name.h"
#include "queue_policy.h"
#include "wav.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>

#include "esp_log.h"
#include "nvs.h"

static const char *TAG = "queue";

#define QUEUE_ABS STORAGE_MOUNT "/" SB_QUEUE_DIR
#define REJECTED_ABS STORAGE_MOUNT "/" SB_QUEUE_REJECTED_DIR

void queue_abs_path(const char *name, char *out, size_t n)
{
    snprintf(out, n, "%s/%s", QUEUE_ABS, name);
}

static bool ends_with(const char *s, const char *suf)
{
    size_t ls = strlen(s), lf = strlen(suf);
    return ls >= lf && strcmp(s + ls - lf, suf) == 0;
}

static int cmp_str(const void *a, const void *b) { return strcmp((const char *)a, (const char *)b); }

esp_err_t queue_init(void)
{
    if (!storage_mounted()) return ESP_ERR_INVALID_STATE;
    esp_err_t err = storage_mkdir_p(SB_QUEUE_REJECTED_DIR); // crea anche queue/
    if (err != ESP_OK) return err;
    int promoted = queue_recover();
    ESP_LOGI(TAG, "coda pronta (%d .part recuperati)", promoted);
    return ESP_OK;
}

// Patcha l'header di un .part con la dimensione reale e lo rinomina a .wav.
static esp_err_t promote_part(const char *part_abs, uint64_t size)
{
    FILE *f = fopen(part_abs, "r+b");
    if (!f) return ESP_FAIL;
    uint8_t hdr[WAV_HEADER_SIZE];
    wav_header_build(hdr, (uint32_t)(size - WAV_HEADER_SIZE));
    bool ok = fwrite(hdr, 1, sizeof(hdr), f) == sizeof(hdr);
    fclose(f);
    if (!ok) return ESP_FAIL;
    char wav_abs[QUEUE_PATH_MAX + 32];
    strlcpy(wav_abs, part_abs, sizeof(wav_abs));
    wav_abs[strlen(wav_abs) - strlen(".part")] = '\0';
    return rename(part_abs, wav_abs) == 0 ? ESP_OK : ESP_FAIL;
}

int queue_recover(void)
{
    DIR *d = opendir(QUEUE_ABS);
    if (!d) return 0;
    int promoted = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (!ends_with(e->d_name, ".wav.part")) continue;
        char abs[QUEUE_PATH_MAX + 32];
        queue_abs_path(e->d_name, abs, sizeof(abs));
        struct stat st;
        if (stat(abs, &st) != 0) continue;
        if (queue_part_decide((uint64_t)st.st_size) == QUEUE_PART_PROMOTE) {
            if (promote_part(abs, (uint64_t)st.st_size) == ESP_OK) {
                ESP_LOGW(TAG, "recuperata cattura interrotta: %s (%ld byte)", e->d_name, (long)st.st_size);
                promoted++;
            }
        } else {
            ESP_LOGW(TAG, "scartato .part troppo corto: %s (%ld byte)", e->d_name, (long)st.st_size);
            unlink(abs);
        }
    }
    closedir(d);
    return promoted;
}

static uint32_t next_unsynced_seq(void)
{
    nvs_handle_t h;
    uint32_t seq = 0;
    if (nvs_open(SB_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return (uint32_t)(time(NULL) & 0xFFFFFF);
    nvs_get_u32(h, SB_NVS_KEY_CAPSEQ, &seq); // assente -> 0
    seq++;
    nvs_set_u32(h, SB_NVS_KEY_CAPSEQ, seq);
    nvs_commit(h);
    nvs_close(h);
    return seq;
}

static bool exists(const char *abs) { struct stat st; return stat(abs, &st) == 0; }

esp_err_t queue_new_part_path(char *out_path, size_t n, char *out_id, size_t id_n)
{
    if (!storage_mounted()) return ESP_ERR_INVALID_STATE;
    time_t now = time(NULL);
    struct tm utc;
    gmtime_r(&now, &utc);
    char base[CAPTURE_NAME_MAX];
    if (capture_name_rtc_valid(&utc)) capture_name_from_tm(&utc, base, sizeof(base));
    else capture_name_unsynced(next_unsynced_seq(), base, sizeof(base));

    char id[CAPTURE_NAME_MAX + 8];
    strlcpy(id, base, sizeof(id));
    for (int k = 2; k < 100; k++) {
        char wav_abs[QUEUE_PATH_MAX + 32], part_abs[QUEUE_PATH_MAX + 32];
        snprintf(wav_abs, sizeof(wav_abs), "%s/%s.wav", QUEUE_ABS, id);
        snprintf(part_abs, sizeof(part_abs), "%s/%s.wav.part", QUEUE_ABS, id);
        if (!exists(wav_abs) && !exists(part_abs)) {
            strlcpy(out_path, part_abs, n);
            strlcpy(out_id, id, id_n);
            return ESP_OK;
        }
        capture_name_with_suffix(base, k, id, sizeof(id));
    }
    return ESP_FAIL;
}

esp_err_t queue_commit(const char *part_path)
{
    char wav_abs[QUEUE_PATH_MAX + 32];
    strlcpy(wav_abs, part_path, sizeof(wav_abs));
    if (!ends_with(wav_abs, ".part")) return ESP_ERR_INVALID_ARG;
    wav_abs[strlen(wav_abs) - strlen(".part")] = '\0';
    if (rename(part_path, wav_abs) != 0) {
        ESP_LOGE(TAG, "rename %s fallita", part_path);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "in coda: %s", wav_abs);
    return ESP_OK;
}

esp_err_t queue_discard(const char *part_path)
{
    return unlink(part_path) == 0 ? ESP_OK : ESP_FAIL;
}

int queue_list(char names[][QUEUE_PATH_MAX], int max)
{
    DIR *d = opendir(QUEUE_ABS);
    if (!d) return 0;
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL && n < max) {
        if (e->d_type == DT_DIR) continue;
        if (!ends_with(e->d_name, ".wav")) continue;
        strlcpy(names[n++], e->d_name, QUEUE_PATH_MAX);
    }
    closedir(d);
    qsort(names, n, QUEUE_PATH_MAX, cmp_str);
    return n;
}

int queue_count(uint64_t *out_total_bytes)
{
    DIR *d = opendir(QUEUE_ABS);
    if (!d) { if (out_total_bytes) *out_total_bytes = 0; return 0; }
    int n = 0; uint64_t total = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (e->d_type == DT_DIR || !ends_with(e->d_name, ".wav")) continue;
        n++;
        if (out_total_bytes) {
            char abs[QUEUE_PATH_MAX + 32]; struct stat st;
            queue_abs_path(e->d_name, abs, sizeof(abs));
            if (stat(abs, &st) == 0) total += (uint64_t)st.st_size;
        }
    }
    closedir(d);
    if (out_total_bytes) *out_total_bytes = total;
    return n;
}

esp_err_t queue_delete(const char *name)
{
    char abs[QUEUE_PATH_MAX + 32];
    queue_abs_path(name, abs, sizeof(abs));
    return unlink(abs) == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t queue_reject(const char *name)
{
    char from[QUEUE_PATH_MAX + 32], to[QUEUE_PATH_MAX + 48];
    queue_abs_path(name, from, sizeof(from));
    snprintf(to, sizeof(to), "%s/%s", REJECTED_ABS, name);
    unlink(to); // se esiste gia' un omonimo rifiutato, sovrascrivi
    return rename(from, to) == 0 ? ESP_OK : ESP_FAIL;
}
```

Aggiungi `"queue.c"` a `SRCS`. Nota: NVS è già inizializzato da `wifi_connect` (`nvs_init_safe`);
la coda può essere usata **prima** del Wi-Fi, quindi in Task 7 `app_main` chiamerà
`nvs_flash_init()` nel boot base (vedi Task 7 Step 3). Per la prova HIL di questo task
basta che il nome sia `unsynced` o datato: se NVS non è inizializzato `nvs_open` fallisce e
il fallback usa `time(NULL)`.

- [ ] **Step 4: prova HIL — aggiorna la prova temporanea in `app_main.c`**

Sostituisci il blocco di Task 5 Step 4 con:

```c
    #include "queue.h"   // in testa
    ...
    if (mode == BOOT_CAPTURE) {
        ESP_ERROR_CHECK(queue_init());
        char part[QUEUE_PATH_MAX + 32], id[CAPTURE_NAME_MAX + 8];
        ESP_ERROR_CHECK(queue_new_part_path(part, sizeof(part), id, sizeof(id)));
        ESP_ERROR_CHECK(capture_start(part));
        display_text("REC", id);
        int released = 0;
        while (released < 2 && capture_is_running()) {
            vTaskDelay(pdMS_TO_TICKS(50));
            released = power_pwr_pressed() ? 0 : released + 1;
        }
        capture_result_t r; capture_stop(&r);
        if (r.duration_ms >= SB_CAPTURE_MIN_MS) queue_commit(part); else queue_discard(part);
        uint64_t bytes = 0; int n = queue_count(&bytes);
        ESP_LOGI(TAG, "coda: %d file, %llu byte", n, (unsigned long long)bytes);
    }
```

(rimuovi anche le righe `storage_write/read` di `bringup.txt`: non esistono più.)

Run: build + flash (DEV via USER), poi: (a) PWR tenuto 5 s → log `in coda: /sdcard/queue/cap_unsynced_000001.wav`, `coda: 1 file`; (b) PWR tenuto 0,5 s → nessun nuovo file, `coda: 1 file`; (c) togli alimentazione durante una registrazione di ~5 s (stacca USB, se la batteria è collegata stacca anche quella o tieni premuto RESET), riaccendi con PWR tenuto 3 s → log `recuperata cattura interrotta: ... .wav.part`, poi `coda: 3 file`.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/storage.c firmware/main/storage.h firmware/main/queue.c firmware/main/queue.h firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: coda catture come directory su SD con recupero dei .part interrotti"
```

---

### Task 7: `app_main` riscritto — flusso completo con SYNC stub, display a 3 righe

**Files:**
- Modify: `firmware/main/display.h`, `firmware/main/display.c` (aggiungi `display_lines`)
- Rewrite: `firmware/main/app_main.c`
- Modify: `firmware/main/secrets.h.example` (aggiungi `SECONDBRAIN_BASE_URL`), `firmware/main/secrets.h` (locale, non committato)

**Interfaces:**
- Consumes: tutto quanto sopra; `sensors_init/read_battery/read_time`; `wifi_connect`; `ota_pull/ota_dev_server_start/ota_mark_valid_if_pending`.
- Produces: `void display_lines(const char *l1, const char *l2, const char *l3);` e la struttura di `app_main` che i Task 8–10 completano sostituendo gli stub `sync_run`/`timesync_*`/`status_http_*` (segnalati con commento `// TASK N`).

- [ ] **Step 1: `display_lines`**

`display.h`: aggiungi `void display_lines(const char *l1, const char *l2, const char *l3);`
`display.c`, dopo `display_text`:
```c
void display_lines(const char *l1, const char *l2, const char *l3)
{
    if (!s_ready) return;
    memset(s_fb, 0x00, sizeof(s_fb));
    draw_string(s_fb, 8, 56, l1 ? l1 : "");
    draw_string(s_fb, 8, 84, l2 ? l2 : "");
    draw_string(s_fb, 8, 112, l3 ? l3 : "");
    display_blit_1bit(s_fb, DISPLAY_W, DISPLAY_H);
}
```
(font 7×12 con passo 8 px: max 24 caratteri per riga; i testi sotto rispettano il limite.)

- [ ] **Step 2: `secrets.h.example`** — aggiungi:

```c
#define SECONDBRAIN_BASE_URL "http://192.168.1.50:8000"   // server di test (tools/capture_server.py) o backend
```
e nel tuo `secrets.h` locale: `#define SECONDBRAIN_BASE_URL "http://192.168.1.28:8000"`.

- [ ] **Step 3: `firmware/main/app_main.c`** (contenuto completo)

```c
// Flusso di un ciclo (spec Fase 1a §4): boot base -> modalita' -> [CAPTURE] -> SYNC ->
// display stato -> deep sleep. In DEV resta sveglio con push/status server.
#include <stdio.h>
#include <stdbool.h>
#include <string.h>
#include <time.h>
#include <sys/time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "config.h"
#include "fw_version.h"
#include "display.h"
#include "power.h"
#include "storage.h"
#include "audio.h"
#include "sensors.h"
#include "wifi.h"
#include "ota.h"
#include "capture.h"
#include "queue.h"
#include "capture_name.h"
#include "secrets.h"

static const char *TAG = "app";

typedef struct {
    int  battery_pct;
    float battery_v;
    bool sd_ok;
    bool wifi_ok;
    int  sent, rejected, remaining;
    bool server_error;
    char capture_msg[32];   // "Salvato 0:07" | "Scartato" | "Max 10:00" | "SD assente" | ...
} cycle_state_t;

static void nvs_init_early(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) ESP_LOGE(TAG, "nvs init: %s", esp_err_to_name(err));
}

// Ora di sistema dall'RTC (UTC). TASK 8: spostato in timesync_load_rtc().
static void system_time_from_rtc(void)
{
    struct tm t;
    if (sensors_read_time(&t) != ESP_OK) return;
    setenv("TZ", "UTC0", 1); tzset();
    time_t epoch = mktime(&t);
    struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
    settimeofday(&tv, NULL);
    setenv("TZ", SB_TZ, 1); tzset();
    ESP_LOGI(TAG, "ora di sistema da RTC: %04d-%02d-%02d %02d:%02d:%02dZ (valido=%d)",
             t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec,
             capture_name_rtc_valid(&t));
}

static void fmt_mmss(uint32_t ms, char *out, size_t n)
{
    uint32_t s = ms / 1000;
    snprintf(out, n, "%lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
}

static void do_capture(cycle_state_t *st)
{
    if (st->battery_pct >= 0 && st->battery_pct < SB_BATTERY_MIN_RECORD_PCT) {
        strlcpy(st->capture_msg, "Batteria scarica", sizeof(st->capture_msg));
        return;
    }
    if (!st->sd_ok) { strlcpy(st->capture_msg, "SD assente", sizeof(st->capture_msg)); return; }
    uint64_t free_b = 0;
    if (storage_free_bytes(&free_b) == ESP_OK && free_b < SB_SD_MIN_FREE_BYTES) {
        strlcpy(st->capture_msg, "SD piena", sizeof(st->capture_msg));
        return;
    }
    if (audio_init() != ESP_OK) { strlcpy(st->capture_msg, "Errore audio", sizeof(st->capture_msg)); return; }

    char part[QUEUE_PATH_MAX + 32], id[CAPTURE_NAME_MAX + 8];
    if (queue_new_part_path(part, sizeof(part), id, sizeof(id)) != ESP_OK ||
        capture_start(part) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
        return;
    }
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS + SB_CAPTURE_MAX_MS); // §4: registrazione esclusa dai 5 min
    display_text("* REC", id);   // ~2 s bloccanti: il task registratore intanto scrive

    int released = 0;
    while (released < 2 && capture_is_running()) {
        vTaskDelay(pdMS_TO_TICKS(50));
        released = power_pwr_pressed() ? 0 : released + 1;
    }
    capture_result_t r;
    capture_stop(&r);
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS);

    char mmss[16];
    fmt_mmss(r.duration_ms, mmss, sizeof(mmss));
    if (r.duration_ms < SB_CAPTURE_MIN_MS) {
        queue_discard(part);
        strlcpy(st->capture_msg, "Scartato", sizeof(st->capture_msg));
    } else if (queue_commit(part) != ESP_OK) {
        strlcpy(st->capture_msg, "Errore SD", sizeof(st->capture_msg));
    } else if (r.hit_max) {
        snprintf(st->capture_msg, sizeof(st->capture_msg), "Max %s", mmss);
    } else {
        snprintf(st->capture_msg, sizeof(st->capture_msg), "Salvato %s", mmss);
    }
    if (r.err != ESP_OK) ESP_LOGW(TAG, "cattura con errore: %s", esp_err_to_name(r.err));
    ESP_LOGI(TAG, "cattura: %s (%s)", st->capture_msg, id);
}

static void do_sync(cycle_state_t *st)
{
    if (st->battery_pct >= 0 && st->battery_pct < SB_BATTERY_MIN_RECORD_PCT) {
        ESP_LOGW(TAG, "batteria %d%%: niente Wi-Fi", st->battery_pct);
        return;
    }
    if (wifi_connect(SB_WIFI_BUDGET_MS) != ESP_OK) { ESP_LOGW(TAG, "no wifi"); return; }
    st->wifi_ok = true;

    // TASK 8: timesync_run() qui (SNTP -> RTC se necessario).
    // TASK 9: sync_run(SB_SYNC_WINDOW_MS, &result) qui; per ora la coda resta com'e'.
    st->remaining = st->sd_ok ? queue_count(NULL) : 0;

    if (st->battery_pct < 0 || st->battery_pct >= SB_BATTERY_MIN_OTA_PCT) {
        esp_err_t r = ota_pull(OTA_MANIFEST_URL);   // non ritorna se aggiorna
        ESP_LOGI(TAG, "ota_pull -> %s", esp_err_to_name(r));
    }
}

static void show_status(const cycle_state_t *st)
{
    char l1[32], l2[32], l3[32];
    time_t now = time(NULL); struct tm lt; struct tm utc;
    gmtime_r(&now, &utc); localtime_r(&now, &lt);
    if (capture_name_rtc_valid(&utc)) snprintf(l1, sizeof(l1), "%02d:%02d", lt.tm_hour, lt.tm_min);
    else strlcpy(l1, "--:--", sizeof(l1));
    if (st->battery_pct >= 0) snprintf(l2, sizeof(l2), "coda: %d  bat %d%%", st->remaining, st->battery_pct);
    else snprintf(l2, sizeof(l2), "coda: %d", st->remaining);
    if (st->capture_msg[0] && !st->wifi_ok) snprintf(l3, sizeof(l3), "%s / no wifi", st->capture_msg);
    else if (!st->wifi_ok) strlcpy(l3, "no wifi", sizeof(l3));
    else if (st->server_error) strlcpy(l3, "server ko", sizeof(l3));
    else if (st->sent > 0) snprintf(l3, sizeof(l3), "%d inviate", st->sent);
    else strlcpy(l3, "sync ok", sizeof(l3));
    display_lines(l1, l2, l3);
    ESP_LOGI(TAG, "stato: [%s] [%s] [%s]", l1, l2, l3);
}

static void dev_mode(void)
{
    power_cycle_deadline_cancel();
    if (wifi_connect(SB_WIFI_BUDGET_MS * 2) != ESP_OK) {
        display_text("DEV MODE", "no wifi");
        ESP_LOGW(TAG, "DEV senza Wi-Fi");
    } else {
        char ip[16]; wifi_get_ip(ip, sizeof(ip));
        if (ota_dev_server_start() == ESP_OK) {
            // TASK 10: status_http_register() qui.
            ESP_LOGI(TAG, "DEV OTA ready: curl --data-binary @build/secondbrain_fw.bin http://%s/ota", ip);
        }
        display_text("DEV MODE", ip);
    }
    while (true) {
        ESP_LOGI(TAG, "alive (DEV mode, no sleep)");
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());
    setenv("TZ", SB_TZ, 1); tzset();

    // --- boot base ---
    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", power_boot_mode_name(mode));
    ESP_ERROR_CHECK(display_init());
    ota_mark_valid_if_pending();           // self-check: boot + display ok
    power_cycle_deadline_start(SB_CYCLE_DEADLINE_MS);
    nvs_init_early();

    if (mode == BOOT_DEV) {
        display_text("secondbrain", "DEV MODE");
        dev_mode();                        // non ritorna
    }

    cycle_state_t st = { .battery_pct = -1, .remaining = 0 };
    if (sensors_init() == ESP_OK) {
        if (sensors_read_battery(&st.battery_v, &st.battery_pct) != ESP_OK) st.battery_pct = -1;
        system_time_from_rtc();
    }
    ESP_LOGI(TAG, "batteria: %.2fV (%d%%)", st.battery_v, st.battery_pct);

    st.sd_ok = (storage_mount() == ESP_OK) && (queue_init() == ESP_OK);
    if (!st.sd_ok) ESP_LOGW(TAG, "SD non disponibile");

    if (mode == BOOT_CAPTURE) do_capture(&st);
    else display_text("secondbrain", fw_version());

    do_sync(&st);
    if (st.sd_ok) st.remaining = queue_count(NULL);

    show_status(&st);
    vTaskDelay(pdMS_TO_TICKS(300));
    power_deep_sleep();
}
```

Nota su `mktime` con `TZ=UTC0`: newlib rispetta `TZ` dopo `tzset()`; usare `TZ=UTC0`
temporaneamente rende `mktime` equivalente a `timegm`. Il Task 8 lo sostituisce con
`timesync_tm_to_epoch_utc` (già testato su host).

- [ ] **Step 4: build + HIL (scenari 1, 4, 5 della spec §11, senza upload)**

Run: `cd firmware && idf.py build 2>&1 | grep -E 'error|warning: |binary size'` → zero warning.
Flash (DEV via USER). Poi:
1. PWR tenuto 3 s: display `* REC` + id, poi stato `--:--` / `coda: N  bat 9x%` / `Salvato 0:0x / no wifi` oppure `Salvato 0:0x` + `sync ok` se il Wi-Fi c'è (il server ancora non esiste, quindi `sync ok` con `coda: N` ≥ 1: giusto per ora).
2. PWR < 1 s → `Scartato`.
3. SD estratta, PWR tenuto 3 s → `SD assente`, ciclo terminato in sleep, **nessun reboot loop** nel log.
4. Pressione breve → `SYNC_ONLY`, display stato, sleep.
5. USER entro 1 s → DEV, push server.
Expected: tutto come sopra; tempo di ciclo senza registrazione ≤ 20 s.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/app_main.c firmware/main/display.c firmware/main/display.h firmware/main/secrets.h.example
git commit -m "firmware: app_main con flusso cattura/sync/stato e display a tre righe"
```

---

### Task 8: Time sync — RTC↔sistema, SNTP, scrittura PCF85063

**Files:**
- Modify: `firmware/main/sensors.h`, `firmware/main/sensors.c` (`sensors_set_time`)
- Create: `firmware/main/timesync.h`, `firmware/main/timesync.c`
- Modify: `firmware/main/CMakeLists.txt`, `firmware/main/app_main.c` (sostituisci `system_time_from_rtc` e lo stub TASK 8)

**Interfaces:**
- Consumes: `timesync_policy.h`, `sensors_read_time`, NVS, `esp_netif_sntp`.
- Produces:
  - `esp_err_t sensors_set_time(const struct tm *utc);`
  - `void timesync_load_rtc(void);` — ora di sistema dall'RTC, TZ impostata a `SB_TZ`
  - `esp_err_t timesync_run(void);` — con Wi-Fi su: SNTP se `timesync_needed`, scrive RTC e NVS. Ritorna `ESP_OK` anche se non serviva; errore solo se SNTP fallisce quando serviva.
  - `bool timesync_rtc_valid(void);`

- [ ] **Step 1: `sensors_set_time`**

`sensors.h`: `esp_err_t sensors_set_time(const struct tm *utc);   // scrive il PCF85063 (UTC)`
`sensors.c`, accanto a `sensors_read_time` (usa l'helper di scrittura già presente per il
registro di controllo; se non esiste, usa `i2c_master_write_to_device`):

```c
static uint8_t dec2bcd(int v) { return (uint8_t)(((v / 10) << 4) | (v % 10)); }

esp_err_t sensors_set_time(const struct tm *utc)
{
    if (!s_i2c_ready) return ESP_ERR_INVALID_STATE;
    if (!utc) return ESP_ERR_INVALID_ARG;
    uint8_t buf[8];
    buf[0] = 0x04; // SEC_REG
    buf[1] = dec2bcd(utc->tm_sec) & 0x7F;      // bit7 OS = 0
    buf[2] = dec2bcd(utc->tm_min);
    buf[3] = dec2bcd(utc->tm_hour);
    buf[4] = dec2bcd(utc->tm_mday);
    buf[5] = (uint8_t)(utc->tm_wday & 0x07);
    buf[6] = dec2bcd(utc->tm_mon + 1);
    buf[7] = dec2bcd((utc->tm_year + 1900) % 100);
    esp_err_t err = i2c_master_write_to_device(BOARD_I2C_PORT, BOARD_PCF85063_ADDR, buf, sizeof(buf), pdMS_TO_TICKS(100));
    if (err != ESP_OK) ESP_LOGE(TAG, "pcf85063 write: %s", esp_err_to_name(err));
    return err;
}
```

- [ ] **Step 2: `timesync.h` / `timesync.c`**

```c
#pragma once
#include <stdbool.h>
#include "esp_err.h"
// Ora (spec §6.4): RTC PCF85063 (UTC) <-> ora di sistema; SNTP quando serve.
void      timesync_load_rtc(void);   // al boot: settimeofday dall'RTC, TZ=SB_TZ per il display
bool      timesync_rtc_valid(void);  // anno >= SB_RTC_MIN_VALID_YEAR
esp_err_t timesync_run(void);        // con Wi-Fi: SNTP se necessario -> RTC + NVS lastntp
```

```c
#include "timesync.h"
#include "timesync_policy.h"
#include "capture_name.h"
#include "sensors.h"
#include "config.h"

#include <time.h>
#include <sys/time.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "nvs.h"

static const char *TAG = "timesync";

void timesync_load_rtc(void)
{
    setenv("TZ", SB_TZ, 1); tzset();
    struct tm t;
    if (sensors_read_time(&t) != ESP_OK) { ESP_LOGW(TAG, "RTC non leggibile"); return; }
    struct timeval tv = { .tv_sec = (time_t)timesync_tm_to_epoch_utc(&t), .tv_usec = 0 };
    settimeofday(&tv, NULL);
    ESP_LOGI(TAG, "ora da RTC: %04d-%02d-%02dT%02d:%02d:%02dZ valido=%d",
             t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, timesync_rtc_valid());
}

bool timesync_rtc_valid(void)
{
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc);
    return capture_name_rtc_valid(&utc);
}

static int64_t nvs_get_lastntp(void)
{
    nvs_handle_t h; int64_t v = 0;
    if (nvs_open(SB_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return 0;
    nvs_get_i64(h, SB_NVS_KEY_LASTNTP, &v);
    nvs_close(h);
    return v;
}

static void nvs_set_lastntp(int64_t v)
{
    nvs_handle_t h;
    if (nvs_open(SB_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_i64(h, SB_NVS_KEY_LASTNTP, v); nvs_commit(h); nvs_close(h);
}

esp_err_t timesync_run(void)
{
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc);
    if (!timesync_needed(utc.tm_year + 1900, nvs_get_lastntp(), (int64_t)now)) {
        ESP_LOGI(TAG, "RTC valido e NTP recente: niente da fare");
        return ESP_OK;
    }
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG(SB_NTP_SERVER);
    esp_err_t err = esp_netif_sntp_init(&cfg);
    if (err != ESP_OK) { ESP_LOGE(TAG, "sntp init: %s", esp_err_to_name(err)); return err; }
    err = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(SB_NTP_WAIT_MS));
    esp_netif_sntp_deinit();
    if (err != ESP_OK) { ESP_LOGW(TAG, "SNTP fallito (%s): resto con l'ora dell'RTC", esp_err_to_name(err)); return err; }

    now = time(NULL); gmtime_r(&now, &utc);
    if (sensors_set_time(&utc) == ESP_OK) {
        nvs_set_lastntp((int64_t)now);
        ESP_LOGI(TAG, "RTC aggiornato da NTP: %04d-%02d-%02dT%02d:%02d:%02dZ",
                 utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec);
    }
    return ESP_OK;
}
```

Aggiungi `"timesync.c"` a `SRCS`. In `app_main.c`: elimina `system_time_from_rtc`, chiama
`timesync_load_rtc()` al suo posto, e in `do_sync` sostituisci il commento `// TASK 8` con
`timesync_run();`. Includi `timesync.h`.

- [ ] **Step 3: build + HIL (scenario 7)**

Flash; pressione breve (SYNC_ONLY) con Wi-Fi. Expected nel log: `ora da RTC: 2000-...
valido=0`, poi `RTC aggiornato da NTP: 2026-...`. Secondo ciclo: `ora da RTC: 2026-...
valido=1`, `RTC valido e NTP recente: niente da fare`; il display mostra l'ora locale
corretta; una nuova cattura si chiama `cap_2026....wav`.
Se SNTP fallisce con DNS: metti in `secrets.h` `#define SB_NTP_SERVER_OVERRIDE "192.168.1.1"`
e in `config.h` `#ifdef SB_NTP_SERVER_OVERRIDE #undef SB_NTP_SERVER #define SB_NTP_SERVER SB_NTP_SERVER_OVERRIDE #endif` (spec §13).

- [ ] **Step 4: Commit**

```bash
git add firmware/main/sensors.c firmware/main/sensors.h firmware/main/timesync.c firmware/main/timesync.h firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: ora di sistema dall'RTC e sincronizzazione NTP con scrittura del PCF85063"
```

---

### Task 9: Server di test `POST /captures` e upload della coda (`sync.c`)

**Files:**
- Create: `firmware/tools/capture_server.py`
- Create: `firmware/main/sync.h`, `firmware/main/sync.c`
- Modify: `firmware/main/CMakeLists.txt`, `firmware/main/app_main.c` (stub TASK 9), `firmware/.gitignore` (`captures_inbox/`)

**Interfaces:**
- Consumes: `queue.h`, `sync_policy.h`, `wav.h`, `sensors_read_battery`, `fw_version`, `SECONDBRAIN_BASE_URL`.
- Produces:
  - `typedef struct { int sent; int rejected; int remaining; bool server_error; } sync_result_t;`
  - `esp_err_t sync_run(uint32_t window_ms, sync_result_t *out);` — presuppone Wi-Fi connesso e SD montata

- [ ] **Step 1: `firmware/tools/capture_server.py`**

```python
#!/usr/bin/env python3
"""Server di test per l'upload delle catture (spec Fase 1a §6.2).

Uso:
    cd firmware && python3 tools/capture_server.py                 # porta 8000
    cd firmware && python3 tools/capture_server.py --port 8000 --fail-with 500

Riceve POST /captures con corpo WAV grezzo e metadati negli header X-Capture-*,
verifica l'header RIFF/WAVE, salva in captures_inbox/<id>.wav e risponde JSON.
Idempotenza: un id gia' ricevuto -> 409 {"status":"duplicate"}.
--fail-with N forza la risposta N per tutte le POST (test di 400/409/500 lato device).
"""
import argparse, json, http.server, socketserver, struct
from pathlib import Path

INBOX = Path("captures_inbox")

class Handler(http.server.BaseHTTPRequestHandler):
    fail_with = None

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/captures":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        cid = self.headers.get("X-Capture-Id", "").strip()
        meta = {k: v for k, v in self.headers.items() if k.lower().startswith("x-")}
        print(f"POST /captures id={cid!r} bytes={len(body)} meta={meta}", flush=True)

        if Handler.fail_with:
            return self._json(Handler.fail_with, {"id": cid, "status": "forced", "code": Handler.fail_with})
        if not cid or len(body) < 44 or body[0:4] != b"RIFF" or body[8:12] != b"WAVE":
            return self._json(400, {"id": cid, "status": "invalid", "reason": "header WAV o id mancanti"})
        data_size = struct.unpack("<I", body[40:44])[0]
        if data_size != len(body) - 44:
            print(f"  ATTENZIONE: data_size header={data_size} vs reale={len(body)-44}", flush=True)
        INBOX.mkdir(exist_ok=True)
        dst = INBOX / f"{cid}.wav"
        if dst.exists():
            return self._json(409, {"id": cid, "status": "duplicate"})
        dst.write_bytes(body)
        secs = (len(body) - 44) / 32000
        print(f"  salvato {dst} ({secs:.1f} s)", flush=True)
        return self._json(201, {"id": cid, "status": "accepted", "seconds": round(secs, 1)})

    def log_message(self, fmt, *args):  # log compatto
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--fail-with", type=int, default=None, help="forza questo codice HTTP per tutte le POST")
    a = ap.parse_args()
    Handler.fail_with = a.fail_with
    with socketserver.TCPServer(("", a.port), Handler) as srv:
        srv.allow_reuse_address = True
        print(f"capture_server su :{a.port} -> {INBOX.resolve()} (fail_with={a.fail_with})", flush=True)
        srv.serve_forever()

if __name__ == "__main__":
    main()
```

Aggiungi `captures_inbox/` a `firmware/.gitignore`. Prova dal Mac:
`cd firmware && python3 tools/capture_server.py &` poi
`python3 -c "import struct;open('/tmp/t.wav','wb').write(b'RIFF'+struct.pack('<I',36+64000)+b'WAVEfmt '+struct.pack('<IHHIIHH',16,1,1,16000,32000,2,16)+b'data'+struct.pack('<I',64000)+bytes(64000))"` e
`curl -s -w '\n%{http_code}\n' -H 'X-Capture-Id: cap_test_1' -H 'Content-Type: audio/wav' --data-binary @/tmp/t.wav http://192.168.1.28:8000/captures` → `201 accepted`, ripetuto → `409`.

- [ ] **Step 2: `sync.h` / `sync.c`**

```c
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
// Upload della coda verso POST {SECONDBRAIN_BASE_URL}/captures (spec §6.2-6.3).
typedef struct {
    int  sent;          // file accettati (200/201/409) e cancellati
    int  rejected;      // file spostati in rejected/ (altri 4xx)
    int  remaining;     // file ancora in coda alla fine
    bool server_error;  // sync interrotto per 5xx/timeout/rete
} sync_result_t;

// Presuppone Wi-Fi connesso e SD montata. Non inizia nuovi upload oltre window_ms.
esp_err_t sync_run(uint32_t window_ms, sync_result_t *out);
```

```c
#include "sync.h"
#include "sync_policy.h"
#include "queue.h"
#include "wav.h"
#include "config.h"
#include "fw_version.h"
#include "sensors.h"
#include "secrets.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_mac.h"
#include "esp_timer.h"

static const char *TAG = "sync";
#define SYNC_CHUNK 4096
#define SYNC_MAX_FILES 64

// Ritorna il codice HTTP (>0) oppure <=0 su errore di rete/timeout.
static int upload_one(const char *name, const char *device_id, const char *bat_pct, const char *bat_v)
{
    char abs[QUEUE_PATH_MAX + 32];
    queue_abs_path(name, abs, sizeof(abs));
    struct stat st;
    if (stat(abs, &st) != 0) return 400; // sparito: trattalo come non valido (rejected fallira' e passera' oltre)
    FILE *f = fopen(abs, "rb");
    if (!f) return 400;

    char id[QUEUE_PATH_MAX];
    strlcpy(id, name, sizeof(id));
    char *dot = strstr(id, ".wav"); if (dot) *dot = '\0';

    char url[192];
    snprintf(url, sizeof(url), "%s/captures", SECONDBRAIN_BASE_URL);
    esp_http_client_config_t cfg = { .url = url, .method = HTTP_METHOD_POST, .timeout_ms = SB_HTTP_IDLE_TIMEOUT_MS };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) { fclose(f); return 0; }

    esp_http_client_set_header(c, "Content-Type", "audio/wav");
    esp_http_client_set_header(c, "X-Capture-Id", id);
    if (strncmp(id, "cap_unsynced_", 13) != 0 && strlen(id) >= 19) {
        // cap_YYYYMMDD_HHMMSS[...] -> YYYY-MM-DDTHH:MM:SSZ
        char ts[24];
        snprintf(ts, sizeof(ts), "%.4s-%.2s-%.2sT%.2s:%.2s:%.2sZ", id + 4, id + 8, id + 10, id + 13, id + 15, id + 17);
        esp_http_client_set_header(c, "X-Capture-Ts", ts);
    }
    esp_http_client_set_header(c, "X-Device-Id", device_id);
    esp_http_client_set_header(c, "X-Firmware-Version", fw_version());
    if (bat_pct[0]) esp_http_client_set_header(c, "X-Battery-Pct", bat_pct);
    if (bat_v[0])   esp_http_client_set_header(c, "X-Battery-Voltage", bat_v);

    int status = 0;
    esp_err_t err = esp_http_client_open(c, (int)st.st_size);
    if (err != ESP_OK) { ESP_LOGE(TAG, "open %s: %s", url, esp_err_to_name(err)); goto out; }

    uint8_t *buf = malloc(SYNC_CHUNK);
    if (!buf) goto out;
    size_t n; bool write_ok = true;
    while ((n = fread(buf, 1, SYNC_CHUNK, f)) > 0) {
        if (esp_http_client_write(c, (const char *)buf, (int)n) != (int)n) { write_ok = false; break; }
    }
    free(buf);
    if (!write_ok) { ESP_LOGE(TAG, "write interrotta su %s", name); goto out; }
    if (esp_http_client_fetch_headers(c) < 0) { ESP_LOGE(TAG, "fetch_headers fallita su %s", name); goto out; }
    status = esp_http_client_get_status_code(c);
    // consuma il corpo (piccolo JSON) per chiudere pulito
    char tmp[128]; while (esp_http_client_read(c, tmp, sizeof(tmp)) > 0) {}
out:
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    fclose(f);
    ESP_LOGI(TAG, "upload %s (%ld byte) -> HTTP %d", name, (long)st.st_size, status);
    return status;
}

esp_err_t sync_run(uint32_t window_ms, sync_result_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    char (*names)[QUEUE_PATH_MAX] = calloc(SYNC_MAX_FILES, QUEUE_PATH_MAX);
    if (!names) return ESP_ERR_NO_MEM;
    int n = queue_list(names, SYNC_MAX_FILES);
    ESP_LOGI(TAG, "coda: %d file da inviare", n);

    uint8_t mac[6]; char device_id[13] = "unknown";
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK)
        snprintf(device_id, sizeof(device_id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    char bat_pct[8] = "", bat_v[8] = "";
    float v = 0; int pct = 0;
    if (sensors_read_battery(&v, &pct) == ESP_OK) { snprintf(bat_pct, sizeof(bat_pct), "%d", pct); snprintf(bat_v, sizeof(bat_v), "%.2f", v); }

    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < n; i++) {
        if ((esp_timer_get_time() - t0) / 1000 > (int64_t)window_ms) { ESP_LOGW(TAG, "finestra di sync esaurita"); break; }
        int status = upload_one(names[i], device_id, bat_pct, bat_v);
        switch (sync_decide(status)) {
        case SYNC_ACTION_DELETE: queue_delete(names[i]); out->sent++; break;
        case SYNC_ACTION_REJECT: queue_reject(names[i]); out->rejected++; ESP_LOGW(TAG, "%s rifiutato (HTTP %d)", names[i], status); break;
        case SYNC_ACTION_STOP:   out->server_error = true; ESP_LOGW(TAG, "sync interrotto (HTTP %d)", status); i = n; break;
        }
    }
    free(names);
    out->remaining = queue_count(NULL);
    ESP_LOGI(TAG, "sync: inviate=%d rifiutate=%d restano=%d server_error=%d", out->sent, out->rejected, out->remaining, out->server_error);
    return out->server_error ? ESP_FAIL : ESP_OK;
}
```

Aggiungi `"sync.c"` a `SRCS`. In `app_main.c` `do_sync`, al posto del commento `// TASK 9`:

```c
    if (st->sd_ok) {
        sync_result_t r;
        sync_run(SB_SYNC_WINDOW_MS, &r);
        st->sent = r.sent; st->rejected = r.rejected; st->remaining = r.remaining; st->server_error = r.server_error;
    }
```
(includi `sync.h`; togli la riga `st->remaining = st->sd_ok ? queue_count(NULL) : 0;`).

- [ ] **Step 3: build + HIL (scenari 1, 2, 6)**

1. Server spento. Tre catture (2 s, 10 s, 30 s): `Salvato`, `server ko` (connessione rifiutata → STOP), `coda: 3`.
2. `python3 tools/capture_server.py`. Pressione breve: log `upload ... -> HTTP 201` ×3, display `3 inviate`, `coda: 0`. Sul Mac `captures_inbox/*.wav`: `python3 -c "import wave,glob;[print(f, wave.open(f).getnframes()/16000) for f in glob.glob('captures_inbox/*.wav')]"` → durate ≈ quelle tenute (±2 s).
3. `--fail-with 500` + una cattura: file resta, `server ko`. `--fail-with 400`: file in `queue/rejected/`, display `sync ok`/`coda: 0`. Server normale, poi una seconda POST dello stesso id (ricopia un file di `captures_inbox` nella `queue/` della SD, o usa `--fail-with 409`): file cancellato, contato in `inviate`.

- [ ] **Step 4: Commit**

```bash
git add firmware/tools/capture_server.py firmware/main/sync.c firmware/main/sync.h firmware/main/CMakeLists.txt firmware/main/app_main.c firmware/.gitignore
git commit -m "firmware: upload della coda verso POST /captures con ack e server di test"
```

---

### Task 10: `GET /status` in DEV mode

**Files:**
- Modify: `firmware/main/ota.h`, `firmware/main/ota.c` (`ota_dev_server_register`)
- Create: `firmware/main/status_http.h`, `firmware/main/status_http.c`
- Modify: `firmware/main/CMakeLists.txt`, `firmware/main/app_main.c` (stub TASK 10)

**Interfaces:**
- Consumes: `queue_list/queue_count`, `sensors_read_battery`, `timesync_rtc_valid`, cJSON.
- Produces: `esp_err_t ota_dev_server_register(const httpd_uri_t *uri);`, `esp_err_t status_http_register(void);`

- [ ] **Step 1: `ota.c`** — rendi il server accessibile

Sostituisci in `ota_dev_server_start` la variabile locale `httpd_handle_t server = NULL;`
con una statica a livello file `static httpd_handle_t s_dev_server = NULL;` (usala al posto
di `server`), e aggiungi:

```c
esp_err_t ota_dev_server_register(const httpd_uri_t *uri)
{
    if (!s_dev_server) return ESP_ERR_INVALID_STATE;
    return httpd_register_uri_handler(s_dev_server, uri);
}
```
In `ota.h`: `#include "esp_http_server.h"` e `esp_err_t ota_dev_server_register(const httpd_uri_t *uri);`.

- [ ] **Step 2: `status_http.h` / `status_http.c`**

```c
#pragma once
#include "esp_err.h"
// GET /status in DEV mode (spec §8): JSON con versione, batteria, ora, coda.
esp_err_t status_http_register(void);
```

```c
#include "status_http.h"
#include "ota.h"
#include "queue.h"
#include "storage.h"
#include "sensors.h"
#include "timesync.h"
#include "fw_version.h"

#include <stdlib.h>
#include <time.h>
#include "esp_log.h"
#include "cJSON.h"

static const char *TAG = "status";

static esp_err_t status_get(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "version", fw_version());
    float v = 0; int pct = -1;
    if (sensors_init() == ESP_OK && sensors_read_battery(&v, &pct) == ESP_OK) {
        cJSON_AddNumberToObject(root, "battery_pct", pct);
        cJSON_AddNumberToObject(root, "battery_v", (double)((int)(v * 100)) / 100.0);
    }
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc);
    char ts[24];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &utc);
    cJSON_AddStringToObject(root, "time", ts);
    cJSON_AddBoolToObject(root, "rtc_valid", timesync_rtc_valid());

    cJSON *arr = cJSON_AddArrayToObject(root, "queue");
    uint64_t bytes = 0;
    if (storage_mount() == ESP_OK && queue_init() == ESP_OK) {
        char (*names)[QUEUE_PATH_MAX] = calloc(64, QUEUE_PATH_MAX);
        if (names) {
            int n = queue_list(names, 64);
            for (int i = 0; i < n; i++) cJSON_AddItemToArray(arr, cJSON_CreateString(names[i]));
            free(names);
        }
        queue_count(&bytes);
        cJSON_AddBoolToObject(root, "sd", true);
    } else {
        cJSON_AddBoolToObject(root, "sd", false);
    }
    cJSON_AddNumberToObject(root, "queue_bytes", (double)bytes);

    char *txt = cJSON_PrintUnformatted(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, txt ? txt : "{}");
    free(txt);
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t status_http_register(void)
{
    const httpd_uri_t uri = { .uri = "/status", .method = HTTP_GET, .handler = status_get };
    esp_err_t err = ota_dev_server_register(&uri);
    if (err == ESP_OK) ESP_LOGI(TAG, "GET /status registrato");
    return err;
}
```

Aggiungi `"status_http.c"` a `SRCS`. In `app_main.c` `dev_mode`, al posto di `// TASK 10`:
`status_http_register();` (includi `status_http.h`).

- [ ] **Step 3: build + HIL**

Flash, DEV via USER, `curl -s http://192.168.1.119/status | python3 -m json.tool`.
Expected: JSON con `version`, `battery_pct`, `time`, `rtc_valid`, `queue` (lista) e
`queue_bytes`; con SD estratta `"sd": false` e coda vuota, nessun crash.

- [ ] **Step 4: Commit**

```bash
git add firmware/main/ota.c firmware/main/ota.h firmware/main/status_http.c firmware/main/status_http.h firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: GET /status in DEV mode con stato coda, batteria e ora"
```

---

### Task 11: Verifica di accettazione Fase 1a e chiusura

**Files:**
- Modify: `docs/specs/2026-09-23-firmware-capture-sync-design.md` (Stato: → "implementato e verificato il <data>"; nota su eventuali deviazioni emerse)
- Modify: memoria del progetto (fuori repo) a cura di chi esegue

- [ ] **Step 1: pulizia** — `grep -rn "TASK [0-9]" firmware/main/` deve non trovare nulla; `grep -rn "bringup\|audio_record_wav\|storage_write\|storage_read" firmware/main/` idem. Test host: tutti verdi (`21 Tests 0 Failures`). Build senza warning.

- [ ] **Step 2: scenario di accettazione completo (spec §11)** — esegui in sequenza su un device flashato via USB con la build finale, `version.txt` 0.1.0, server di test acceso solo dove indicato:

| # | Azione | Atteso |
|---|---|---|
| 1 | server spento; 3 catture (2 s, 10 s, 30 s) | `Salvato 0:02/0:10/0:30`, `server ko`, `coda: 3`; in DEV `GET /status` le elenca |
| 2 | server acceso; pressione breve | `3 inviate`, `coda: 0`; 3 WAV in `captures_inbox/` riproducibili con durata giusta |
| 3 | cattura ~5 s, togliendo alimentazione a metà; poi PWR breve | log `recuperata cattura interrotta`, upload `HTTP 201` di quel file |
| 4 | PWR < 1 s | `Scartato`, nessun file |
| 5 | SD estratta, PWR tenuto 3 s | `SD assente`, nessun crash, sleep |
| 6 | `--fail-with 500` / `400` / `409` | resta in coda + `server ko` / in `rejected/` / cancellato |
| 7 | dopo il primo sync | RTC corretto; nomi file datati; display con ora locale |
| 8 | server OTA di Fase 0 con manifest più nuovo, batteria ≥ 30 % | pull e reboot sulla nuova versione, poi confermata; DEV via USER + push funzionanti |

- [ ] **Step 3: aggiorna lo stato nella spec e committa**

```bash
git add docs/specs/2026-09-23-firmware-capture-sync-design.md
git commit -m "docs: Fase 1a firmware verificata su hardware, stato spec aggiornato"
```

---

## Self-Review

- **Copertura spec:** §4 flusso e modalità (Task 4, 7) ✓; §5.1 latenza/ordine (Task 7 `do_capture`) ✓; §5.2 task registratore (Task 5) ✓; §5.3 nomi (Task 2, 6) ✓; §5.4 controlli pre-registrazione (Task 7) ✓; §5.5 `.part` e recupero (Task 3, 5, 6) ✓; §6.1 directory (Task 6) ✓; §6.2 contratto HTTP + azioni (Task 3, 9) ✓; §6.3 budget (config.h Task 1, Task 7/9) ✓; §6.4 time sync (Task 3, 8) ✓; §6.5 OTA a fine sync con soglia (Task 7) ✓; §7 display 3 righe con ora locale (Task 7) ✓; §8 DEV con USER e `GET /status` (Task 4, 10) ✓; §9 struttura moduli ✓ (nomi allineati alla tabella File Structure); §10 config/segreti (Task 1, 7) ✓; §11 test host (Task 1–3) e HIL per task + accettazione (Task 11) ✓; §13 rischio NTP/DNS (Task 8 Step 3) ✓; vincolo scoperto in pianificazione: FAT 8.3 → LFN (Task 1) ✓.
- **Placeholder:** nessun TBD; ogni step di codice ha il codice. L'unico "a cura di chi esegue" è la memoria fuori repo (Task 11).
- **Type consistency:** `capture_result_t{data_bytes,duration_ms,hit_max,err}` (Task 5) usata in Task 6/7 ✓; `queue_new_part_path(out_path,n,out_id,id_n)` (Task 6) usata in Task 7 ✓; `sync_result_t{sent,rejected,remaining,server_error}` (Task 9) mappata in `cycle_state_t` (Task 7) ✓; `sync_decide` ritorna `sync_action_t` con i 3 valori usati nello `switch` di Task 9 ✓; `power_pwr_pressed/power_user_pressed/power_boot_mode_name/power_cycle_deadline_*` (Task 4) usate in Task 5/7 ✓; `display_lines(l1,l2,l3)` (Task 7) ✓; `timesync_load_rtc/timesync_run/timesync_rtc_valid` (Task 8) usate in Task 7/10 ✓; `ota_dev_server_register(const httpd_uri_t*)` (Task 10) ✓; `storage_mounted/storage_free_bytes/storage_mkdir_p` (Task 6) usate in Task 7/10 ✓.
