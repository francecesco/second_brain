# Firmware Fase 0 — Bring-up + OTA — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Portare in vita la scheda Waveshare ESP32-S3-ePaper-1.54 (display, audio, SD, sensori, deep sleep, Wi-Fi) e abilitare l'aggiornamento firmware OTA (pull da manifest + push da sviluppo), con rollback automatico, così il device può stare solo alimentato.

**Architecture:** Firmware ESP-IDF v5.x per `esp32s3`, a moduli con responsabilità singola (`display`, `audio`, `storage`, `sensors`, `power`, `wifi`, `ota`) orchestrati da `app_main`. Tutti i pin vivono in `board.h`. OTA con doppia partizione app + `otadata` e rollback del bootloader. La logica pura (manifest, semver) è testata su host-target Linux; le periferiche si validano hardware-in-the-loop con criteri espliciti.

**Tech Stack:** ESP-IDF v5.x, C, CMake, esptool, `esp_ota_ops`/`esp_https_ota`, `esp_http_server`, `esp_vfs_fat_sdmmc`, `esp_wifi`, `driver/i2c_master`, `esp_adc`, componente ES8311, driver e-Paper Waveshare. Python 3 (`http.server`) per il server firmware di test. Unity per gli unit test host.

**Spec:** `docs/specs/2026-09-16-firmware-bringup-ota-design.md`

## Global Constraints

- SDK **ESP-IDF v5.x**, target **`esp32s3`**. Da installare sul Mac (assente).
- Tutto il firmware vive in `second_brain/firmware/`. Nessun file fuori dalla cartella del progetto.
- Porta seriale del device: **`/dev/cu.usbmodem1101`** (USB-Serial/JTAG nativa).
- Chip: ESP32-S3-PICO-1, **8 MB flash embedded**, 8 MB PSRAM.
- **Tutti i pin/GPIO** solo in `main/board.h` (macro `BOARD_*`); nessun numero di pin sparso nei moduli.
- Segreti (`WIFI_SSID`, `WIFI_PASSWORD`, `OTA_MANIFEST_URL`) in `main/secrets.h` **gitignored**, generato da `main/secrets.h.example`. Mai committare `secrets.h`.
- Formato audio: **WAV 16 kHz mono**. Display: **200×200 1-bit**.
- OTA: partizioni `ota_0`/`ota_1` + `otadata`; confronto **semver**; verifica **sha256**; `esp_ota_mark_app_valid_cancel_rollback()` dopo self-check.
- Git: identità **locale** `francecesco <francecesco78@gmail.com>`. **Nessuna attribuzione all'assistente** nei commit.
- Versione firmware embeddata a build-time da `firmware/version.txt`.

---

## File Structure

```
firmware/
├── CMakeLists.txt              # progetto IDF; embedda version.txt
├── sdkconfig.defaults          # target s3, partition table custom, PSRAM, ecc.
├── partitions.csv              # nvs, otadata, phy_init, ota_0, ota_1
├── version.txt                 # es. "0.1.0"
├── .gitignore                  # build/, secrets.h, sdkconfig
├── main/
│   ├── CMakeLists.txt          # idf_component_register + EMBED_TXTFILES
│   ├── idf_component.yml        # dipendenze managed (es8311, epaper se disponibili)
│   ├── app_main.c              # macchina a stati + scelta boot mode
│   ├── board.h                 # pin-map BOARD_* (da repo Waveshare)
│   ├── secrets.h.example       # template segreti
│   ├── fw_version.h/.c         # espone la versione embeddata
│   ├── display.h/.c            # e-Paper 200×200 1-bit
│   ├── storage.h/.c            # SD/FAT32
│   ├── audio.h/.c              # ES8311 + I2S → WAV su SD
│   ├── sensors.h/.c            # SHTC3, RTC PCF85063, ADC batteria
│   ├── power.h/.c              # deep sleep + wake + lettura boot mode
│   ├── wifi.h/.c               # connessione Wi-Fi
│   ├── ota_manifest.h/.c       # parsing manifest + confronto semver (logica pura)
│   └── ota.h/.c                # pull, push dev (POST /ota), rollback confirm
├── host_test/                  # unit test host-target della logica pura
│   ├── CMakeLists.txt
│   └── main/
│       ├── CMakeLists.txt
│       └── test_ota_manifest.c
└── tools/
    └── serve_firmware.py       # server statico manifest + .bin per test pull
```

`ota_manifest` è separato da `ota` apposta: contiene **solo** logica pura (nessuna
API ESP-IDF), così è compilabile e testabile sull'host Linux.

---

### Task 1: Toolchain ESP-IDF + scheletro progetto + "hello"

Installa ESP-IDF, crea lo scheletro minimo del progetto e verifica l'intera pipeline
build → flash → monitor sull'hardware reale.

**Files:**
- Create: `firmware/CMakeLists.txt`
- Create: `firmware/sdkconfig.defaults`
- Create: `firmware/version.txt`
- Create: `firmware/.gitignore`
- Create: `firmware/main/CMakeLists.txt`
- Create: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: —
- Produces: un'app IDF flashabile; `app_main()` come entrypoint.

- [ ] **Step 1: Installa ESP-IDF v5.x** (una tantum sul Mac)

```bash
mkdir -p ~/esp && cd ~/esp
git clone -b v5.3.1 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf && ./install.sh esp32s3
# Attiva l'ambiente in OGNI nuova shell che compila:
. ~/esp/esp-idf/export.sh
```

- [ ] **Step 2: `firmware/version.txt`**

```
0.1.0
```

- [ ] **Step 3: `firmware/CMakeLists.txt`**

```cmake
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(secondbrain_fw)
```

- [ ] **Step 4: `firmware/sdkconfig.defaults`**

```
CONFIG_IDF_TARGET="esp32s3"
CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y
CONFIG_PARTITION_TABLE_CUSTOM=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"
CONFIG_SPIRAM=y
CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
CONFIG_ESP_TASK_WDT_INIT=n
```

- [ ] **Step 5: `firmware/.gitignore`**

```
build/
sdkconfig
sdkconfig.old
main/secrets.h
managed_components/
dependencies.lock
```

- [ ] **Step 6: `firmware/main/CMakeLists.txt`**

```cmake
idf_component_register(
    SRCS "app_main.c"
    INCLUDE_DIRS "."
)
```

- [ ] **Step 7: `firmware/main/app_main.c`**

```c
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "secondbrain firmware bring-up: hello");
    while (true) {
        ESP_LOGI(TAG, "alive");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
```

- [ ] **Step 8: Build**

Run: `cd firmware && idf.py set-target esp32s3 && idf.py build`
Expected: build completa senza errori; genera `build/secondbrain_fw.bin`.

- [ ] **Step 9: Flash + monitor sull'hardware**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: sul monitor seriale compare ripetuto `I (...) app: alive`. (Esci con `Ctrl-]`.)

- [ ] **Step 10: Commit**

```bash
git add firmware/CMakeLists.txt firmware/sdkconfig.defaults firmware/version.txt \
        firmware/.gitignore firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: scaffold progetto ESP-IDF e hello su seriale"
```

---

### Task 2: Pin-map (`board.h`), partizioni OTA e versione embeddata

Ricava la pin-map reale dal riferimento Waveshare, definisci la tabella partizioni
per l'OTA ed esponi la versione firmware in codice.

**Files:**
- Create: `firmware/main/board.h`
- Create: `firmware/partitions.csv`
- Create: `firmware/main/fw_version.h`
- Create: `firmware/main/fw_version.c`
- Create: `firmware/main/secrets.h.example`
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: —
- Produces:
  - `board.h`: macro `BOARD_*` per tutti i pin (display SPI, e-Paper DC/RST/BUSY/CS, SD SPI/SDMMC, I2C SDA/SCL per SHTC3+RTC, I2S per ES8311, ADC batteria, tasti).
  - `partitions.csv` con `ota_0`/`ota_1`/`otadata`.
  - `fw_version.h`: `const char *fw_version(void);` → stringa da `version.txt`.

- [ ] **Step 1: Ottieni la pin-map dal riferimento Waveshare**

```bash
cd /private/tmp/claude-501/*/scratchpad 2>/dev/null || cd /tmp
git clone --depth 1 https://github.com/waveshareteam/ESP32-S3-ePaper-1.54G.git
# Leggi gli esempi/driver: pin display (SCK/MOSI/CS/DC/RST/BUSY), I2C (SDA/SCL),
# I2S ES8311 (MCLK/BCLK/WS/DOUT/DIN), SD (CMD/CLK/D0..), ADC batteria, GPIO tasti.
```
Registra ogni pin trovato come macro in `board.h` (Step 2). Se un pin non è nel repo,
ricavalo dallo schematico/wiki Waveshare della scheda. Nessun numero di pin fuori da `board.h`.

- [ ] **Step 2: `firmware/main/board.h`** (compila i valori con quelli reali dello Step 1)

```c
#pragma once
// Pin-map Waveshare ESP32-S3-ePaper-1.54 — UNICA fonte dei GPIO.
// Valori da confermare/riempire dallo Step 1 (repo/schematico Waveshare).

// e-Paper (SPI)
#define BOARD_EPD_SPI_HOST   SPI2_HOST
#define BOARD_EPD_SCK        /* GPIO */ -1
#define BOARD_EPD_MOSI       /* GPIO */ -1
#define BOARD_EPD_CS         /* GPIO */ -1
#define BOARD_EPD_DC         /* GPIO */ -1
#define BOARD_EPD_RST        /* GPIO */ -1
#define BOARD_EPD_BUSY       /* GPIO */ -1

// I2C (SHTC3 + RTC PCF85063)
#define BOARD_I2C_PORT       I2C_NUM_0
#define BOARD_I2C_SDA        /* GPIO */ -1
#define BOARD_I2C_SCL        /* GPIO */ -1
#define BOARD_SHTC3_ADDR     0x70
#define BOARD_PCF85063_ADDR  0x51

// I2S (codec ES8311)
#define BOARD_ES8311_ADDR    0x18
#define BOARD_I2S_MCLK       /* GPIO */ -1
#define BOARD_I2S_BCLK       /* GPIO */ -1
#define BOARD_I2S_WS         /* GPIO */ -1
#define BOARD_I2S_DOUT       /* GPIO */ -1
#define BOARD_I2S_DIN        /* GPIO */ -1

// microSD (SPI o SDMMC secondo scheda)
#define BOARD_SD_CS          /* GPIO */ -1
#define BOARD_SD_SCK         /* GPIO */ -1
#define BOARD_SD_MOSI        /* GPIO */ -1
#define BOARD_SD_MISO        /* GPIO */ -1

// Batteria (ADC) e tasti
#define BOARD_BAT_ADC_CHAN   /* adc_channel_t */ -1
#define BOARD_BAT_DIVIDER    2.0f            // partitore, da confermare
#define BOARD_BTN_USER       /* GPIO */ -1   // tasto dev-mode / wake
#define BOARD_BTN_ACTIVE_LOW 1
```

- [ ] **Step 3: `firmware/partitions.csv`**

```
# Name,   Type, SubType, Offset,  Size, Flags
nvs,      data, nvs,     0x9000,  0x6000,
otadata,  data, ota,     0xf000,  0x2000,
phy_init, data, phy,     0x11000, 0x1000,
ota_0,    app,  ota_0,   0x20000, 0x300000,
ota_1,    app,  ota_1,   ,        0x300000,
```

- [ ] **Step 4: `firmware/main/fw_version.h`**

```c
#pragma once
const char *fw_version(void);
```

- [ ] **Step 5: `firmware/main/fw_version.c`**

```c
#include "fw_version.h"
// version.txt è embeddato come simbolo binario da CMake (Step 7).
extern const char version_txt_start[] asm("_binary_version_txt_start");
const char *fw_version(void) { return version_txt_start; }
```

- [ ] **Step 6: `firmware/main/secrets.h.example`**

```c
#pragma once
// Copia questo file in secrets.h (gitignored) e compila i valori reali.
#define WIFI_SSID        "la-tua-rete"
#define WIFI_PASSWORD    "la-password"
#define OTA_MANIFEST_URL "http://192.168.1.50:8000/firmware/manifest.json"
```

- [ ] **Step 7: Aggiorna `firmware/main/CMakeLists.txt`** (embedda version.txt)

```cmake
idf_component_register(
    SRCS "app_main.c" "fw_version.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "../version.txt"
)
```

- [ ] **Step 8: Logga la versione in `app_main.c`** (sostituisci il corpo di `app_main`)

```c
#include "fw_version.h"
// ...
    ESP_LOGI(TAG, "secondbrain fw v%s", fw_version());
```

- [ ] **Step 9: Build + flash + verifica**

Run: `cd firmware && idf.py build && idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: il monitor stampa `secondbrain fw v0.1.0`; `idf.py partition-table` mostra `ota_0`/`ota_1`.

- [ ] **Step 10: Commit**

```bash
git add firmware/main/board.h firmware/partitions.csv firmware/main/fw_version.h \
        firmware/main/fw_version.c firmware/main/secrets.h.example firmware/main/CMakeLists.txt \
        firmware/main/app_main.c
git commit -m "firmware: pin-map board.h, partizioni OTA e versione embeddata"
```

---

### Task 3: Display e-Paper (200×200 1-bit)

Inizializza l'e-Paper e mostra testo; conferma controller e modalità di refresh.

**Files:**
- Create: `firmware/main/display.h`
- Create: `firmware/main/display.c`
- Modify: `firmware/main/idf_component.yml` (o `components/` con driver Waveshare)
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `board.h`.
- Produces:
  - `display_init(void) -> esp_err_t`
  - `display_text(const char *line1, const char *line2) -> void`
  - `display_blit_1bit(const uint8_t *buf, int w, int h) -> void` (buffer 200×200, 1 bit/pixel)

- [ ] **Step 1: Dichiara il driver e-Paper** in `firmware/main/idf_component.yml`

```yaml
dependencies:
  idf: ">=5.0"
  # Se esiste un componente e-Paper per questo controller nel Component Registry
  # aggiungilo qui; altrimenti copia il driver Waveshare in firmware/components/epaper/.
```

> Il controller esatto (es. SSD168x/UC81xx) e il driver provengono dal repo Waveshare
> (Task 2, Step 1). Usa quel driver per pilotare SPI + DC/RST/BUSY dai pin `BOARD_EPD_*`.

- [ ] **Step 2: `firmware/main/display.h`**

```c
#pragma once
#include "esp_err.h"
#include <stdint.h>

#define DISPLAY_W 200
#define DISPLAY_H 200

esp_err_t display_init(void);
void display_text(const char *line1, const char *line2);
void display_blit_1bit(const uint8_t *buf, int w, int h);
```

- [ ] **Step 3: `firmware/main/display.c`**

Implementa usando il driver Waveshare: `display_init` configura SPI (host
`BOARD_EPD_SPI_HOST`, pin `BOARD_EPD_*`), reset e init del pannello; `display_text`
rasterizza due righe in un framebuffer 200×200 1-bit e lo invia; `display_blit_1bit`
invia un framebuffer già pronto. Refresh pieno per ora (il parziale è un'ottimizzazione
successiva). Restituisci `ESP_OK`/errore da `display_init`.

- [ ] **Step 4: Registra il sorgente** in `firmware/main/CMakeLists.txt`

```cmake
idf_component_register(
    SRCS "app_main.c" "fw_version.c" "display.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "../version.txt"
)
```

- [ ] **Step 5: Chiama il display in `app_main.c`**

```c
#include "display.h"
// dopo il log della versione:
    ESP_ERROR_CHECK(display_init());
    display_text("secondbrain", fw_version());
```

- [ ] **Step 6: Build + flash + verifica (hardware-in-the-loop)**

Run: `cd firmware && idf.py build && idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: sull'e-Paper compaiono "secondbrain" e la versione; il monitor non mostra errori SPI/BUSY. **Criterio Fase 0 tappa 2 soddisfatto.**

- [ ] **Step 7: Commit**

```bash
git add firmware/main/display.h firmware/main/display.c firmware/main/idf_component.yml \
        firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: driver e-Paper e testo di bring-up sul display"
```

---

### Task 4: Tasti + rilevamento boot mode

Rileva il tasto utente e stabilisci se il boot è normale o *dev mode* (tasto premuto
al power-on), usato più avanti per l'OTA push.

**Files:**
- Create: `firmware/main/power.h`
- Create: `firmware/main/power.c`
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `board.h`.
- Produces:
  - `typedef enum { BOOT_NORMAL, BOOT_DEV } boot_mode_t;`
  - `void power_init(void);`
  - `bool power_button_pressed(void);`
  - `boot_mode_t power_boot_mode(void);` (legge il tasto all'avvio)

- [ ] **Step 1: `firmware/main/power.h`** (deep sleep arriva nel Task 8; qui solo tasti/boot mode)

```c
#pragma once
#include <stdbool.h>

typedef enum { BOOT_NORMAL, BOOT_DEV } boot_mode_t;

void power_init(void);
bool power_button_pressed(void);
boot_mode_t power_boot_mode(void);
```

- [ ] **Step 2: `firmware/main/power.c`**

```c
#include "power.h"
#include "board.h"
#include "driver/gpio.h"

void power_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BOARD_BTN_USER,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = BOARD_BTN_ACTIVE_LOW ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE,
        .pull_down_en = BOARD_BTN_ACTIVE_LOW ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE,
    };
    gpio_config(&io);
}

bool power_button_pressed(void)
{
    int lvl = gpio_get_level(BOARD_BTN_USER);
    return BOARD_BTN_ACTIVE_LOW ? (lvl == 0) : (lvl == 1);
}

boot_mode_t power_boot_mode(void)
{
    return power_button_pressed() ? BOOT_DEV : BOOT_NORMAL;
}
```

- [ ] **Step 3: Registra il sorgente** in `firmware/main/CMakeLists.txt` (aggiungi `power.c` a `SRCS`).

- [ ] **Step 4: Usa il boot mode in `app_main.c`**

```c
#include "power.h"
// prima del display:
    power_init();
    boot_mode_t mode = power_boot_mode();
    ESP_LOGI(TAG, "boot mode: %s", mode == BOOT_DEV ? "DEV" : "NORMAL");
    display_text("secondbrain", mode == BOOT_DEV ? "DEV MODE" : fw_version());
```

- [ ] **Step 5: Verifica (hardware-in-the-loop)**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`, poi resetta la scheda tenendo
premuto il tasto utente.
Expected: a riposo il log dice `boot mode: NORMAL`; tenendo il tasto al boot dice `DEV`; l'e-Paper mostra "DEV MODE". **Criterio tappa 3.**

- [ ] **Step 6: Commit**

```bash
git add firmware/main/power.h firmware/main/power.c firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: tasto utente e rilevamento boot mode (normale/dev)"
```

---

### Task 5: Storage microSD (FAT32)

Monta la microSD e verifica scrittura/lettura di un file.

**Files:**
- Create: `firmware/main/storage.h`
- Create: `firmware/main/storage.c`
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `board.h`.
- Produces:
  - `esp_err_t storage_mount(void);` (monta su `/sdcard`)
  - `esp_err_t storage_write(const char *relpath, const uint8_t *data, size_t len);`
  - `esp_err_t storage_read(const char *relpath, uint8_t *buf, size_t buflen, size_t *out_len);`

- [ ] **Step 1: `firmware/main/storage.h`**

```c
#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>

#define STORAGE_MOUNT "/sdcard"

esp_err_t storage_mount(void);
esp_err_t storage_write(const char *relpath, const uint8_t *data, size_t len);
esp_err_t storage_read(const char *relpath, uint8_t *buf, size_t buflen, size_t *out_len);
```

- [ ] **Step 2: `firmware/main/storage.c`**

Monta con `esp_vfs_fat_sdspi_mount` (SD in SPI, pin `BOARD_SD_*`) oppure
`esp_vfs_fat_sdmmc_mount` se la scheda usa SDMMC (confermato in Task 2). `storage_write`
apre `"/sdcard/<relpath>"` in `"wb"`, scrive e chiude; `storage_read` legge fino a
`buflen` byte e imposta `*out_len`. Ritorna `ESP_OK`/errore.

- [ ] **Step 3: Registra il sorgente** in `firmware/main/CMakeLists.txt` (aggiungi `storage.c`).

- [ ] **Step 4: Prova di scrittura/lettura in `app_main.c`**

```c
#include "storage.h"
#include <string.h>
// dopo il display:
    ESP_ERROR_CHECK(storage_mount());
    const char *msg = "hello-sd";
    ESP_ERROR_CHECK(storage_write("bringup.txt", (const uint8_t *)msg, strlen(msg)));
    uint8_t rb[16] = {0}; size_t n = 0;
    ESP_ERROR_CHECK(storage_read("bringup.txt", rb, sizeof(rb), &n));
    ESP_LOGI(TAG, "SD read back (%d): %.*s", (int)n, (int)n, rb);
```

- [ ] **Step 5: Verifica (hardware-in-the-loop)**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: log `SD read back (8): hello-sd`. Estraendo la SD sul Mac esiste `bringup.txt`. **Criterio tappa 4.**

- [ ] **Step 6: Commit**

```bash
git add firmware/main/storage.h firmware/main/storage.c firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: mount microSD FAT32 e I/O file di bring-up"
```

---

### Task 6: Audio ES8311 → WAV 16 kHz mono su SD

Configura il codec ES8311 via I2C/I2S e registra un WAV su SD.

**Files:**
- Create: `firmware/main/audio.h`
- Create: `firmware/main/audio.c`
- Modify: `firmware/main/idf_component.yml` (dipendenza ES8311)
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `board.h`, `storage.h`.
- Produces:
  - `esp_err_t audio_init(void);`
  - `esp_err_t audio_record_wav(const char *relpath, int seconds);` (16 kHz, mono, 16-bit PCM, header WAV corretto)

- [ ] **Step 1: Dipendenza ES8311** in `firmware/main/idf_component.yml`

```yaml
dependencies:
  espressif/es8311: "*"
```

- [ ] **Step 2: `firmware/main/audio.h`**

```c
#pragma once
#include "esp_err.h"

#define AUDIO_SAMPLE_RATE 16000

esp_err_t audio_init(void);
esp_err_t audio_record_wav(const char *relpath, int seconds);
```

- [ ] **Step 3: `firmware/main/audio.c`**

`audio_init`: init I2C per l'ES8311 (`BOARD_ES8311_ADDR`), configura il codec a 16 kHz
mono, crea il canale I2S RX standard (pin `BOARD_I2S_*`). `audio_record_wav`: apre il
file su SD, scrive un header WAV (PCM 16-bit, 16 kHz, 1 canale) con dimensioni calcolate
da `seconds`, legge i campioni I2S in un buffer e li appende, poi chiude. Ritorna
`ESP_OK`/errore.

- [ ] **Step 4: Registra il sorgente** in `firmware/main/CMakeLists.txt` (aggiungi `audio.c`).

- [ ] **Step 5: Registra 3 secondi in `app_main.c`**

```c
#include "audio.h"
// dopo lo storage:
    ESP_ERROR_CHECK(audio_init());
    ESP_LOGI(TAG, "recording 3s...");
    ESP_ERROR_CHECK(audio_record_wav("bringup.wav", 3));
    ESP_LOGI(TAG, "recording done");
```

- [ ] **Step 6: Verifica (hardware-in-the-loop)**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`, parla vicino al mic durante la registrazione.
Expected: log `recording done`; estraendo la SD, `bringup.wav` si riproduce sul Mac (16 kHz mono) e contiene la voce. **Criterio tappa 5.**

- [ ] **Step 7: Commit**

```bash
git add firmware/main/audio.h firmware/main/audio.c firmware/main/idf_component.yml \
        firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: registrazione audio ES8311 in WAV 16kHz mono su SD"
```

---

### Task 7: Sensori — SHTC3, RTC PCF85063, batteria

Leggi temperatura/umidità, data/ora e tensione batteria.

**Files:**
- Create: `firmware/main/sensors.h`
- Create: `firmware/main/sensors.c`
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `board.h`.
- Produces:
  - `esp_err_t sensors_init(void);` (bus I2C condiviso + ADC batteria)
  - `esp_err_t sensors_read_climate(float *temp_c, float *humidity);`
  - `esp_err_t sensors_read_time(struct tm *out);`
  - `esp_err_t sensors_read_battery(float *volts, int *percent);`

- [ ] **Step 1: `firmware/main/sensors.h`**

```c
#pragma once
#include "esp_err.h"
#include <time.h>

esp_err_t sensors_init(void);
esp_err_t sensors_read_climate(float *temp_c, float *humidity);
esp_err_t sensors_read_time(struct tm *out);
esp_err_t sensors_read_battery(float *volts, int *percent);
```

- [ ] **Step 2: `firmware/main/sensors.c`**

`sensors_init`: crea il bus `i2c_master` (SDA/SCL `BOARD_I2C_*`) e configura l'ADC
oneshot su `BOARD_BAT_ADC_CHAN`. `sensors_read_climate`: comando di misura SHTC3
(`BOARD_SHTC3_ADDR`), converte i raw in °C e %RH. `sensors_read_time`: legge i registri
BCD del PCF85063 (`BOARD_PCF85063_ADDR`) in `struct tm`. `sensors_read_battery`: legge
l'ADC, applica `BOARD_BAT_DIVIDER` per i volt e mappa in percentuale LiPo (3.3 V→0%,
4.2 V→100%, clamp). Ritorna `ESP_OK`/errore.

- [ ] **Step 3: Registra il sorgente** in `firmware/main/CMakeLists.txt` (aggiungi `sensors.c`).

- [ ] **Step 4: Leggi i sensori in `app_main.c`**

```c
#include "sensors.h"
// dopo l'audio:
    ESP_ERROR_CHECK(sensors_init());
    float t=0, h=0, v=0; int pct=0;
    if (sensors_read_climate(&t,&h)==ESP_OK) ESP_LOGI(TAG,"temp %.1fC hum %.0f%%", t, h);
    if (sensors_read_battery(&v,&pct)==ESP_OK) ESP_LOGI(TAG,"bat %.2fV (%d%%)", v, pct);
    struct tm now;
    if (sensors_read_time(&now)==ESP_OK) ESP_LOGI(TAG,"rtc %04d-%02d-%02d %02d:%02d",
        now.tm_year+1900, now.tm_mon+1, now.tm_mday, now.tm_hour, now.tm_min);
```

- [ ] **Step 5: Verifica (hardware-in-the-loop)**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: log con temperatura/umidità plausibili, tensione batteria plausibile (~3.7–4.2 V) e data/ora dall'RTC. **Criterio tappa 6.**

- [ ] **Step 6: Commit**

```bash
git add firmware/main/sensors.h firmware/main/sensors.c firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: lettura SHTC3, RTC PCF85063 e tensione batteria"
```

---

### Task 8: Deep sleep + wake su tasto

Aggiungi il deep sleep con risveglio dal tasto utente (modello a batteria).

**Files:**
- Modify: `firmware/main/power.h`
- Modify: `firmware/main/power.c`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `board.h`, `power.h` (Task 4).
- Produces:
  - `void power_deep_sleep(void);` (configura il wake sul tasto ed entra in deep sleep)

- [ ] **Step 1: Estendi `firmware/main/power.h`**

```c
void power_deep_sleep(void);
```

- [ ] **Step 2: Estendi `firmware/main/power.c`**

```c
#include "esp_sleep.h"

void power_deep_sleep(void)
{
    // Wake quando il tasto va al livello attivo (EXT1, low se active-low).
    esp_sleep_enable_ext1_wakeup(1ULL << BOARD_BTN_USER,
        BOARD_BTN_ACTIVE_LOW ? ESP_EXT1_WAKEUP_ANY_LOW : ESP_EXT1_WAKEUP_ANY_HIGH);
    esp_deep_sleep_start();
}
```

- [ ] **Step 3: Entra in deep sleep a fine ciclo (solo boot NORMAL) in `app_main.c`**

```c
// alla fine di app_main, sostituisci il loop "alive" con:
    if (mode == BOOT_NORMAL) {
        ESP_LOGI(TAG, "entering deep sleep; press button to wake");
        display_text("secondbrain", "sleeping");
        vTaskDelay(pdMS_TO_TICKS(500));
        power_deep_sleep();
    }
```

- [ ] **Step 4: Verifica (hardware-in-the-loop)**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: log `entering deep sleep`, poi seriale silenzioso; premendo il tasto la scheda riparte da capo (nuovo boot). Se disponibile, misura la corrente in sleep. **Criterio tappa 7.**

- [ ] **Step 5: Commit**

```bash
git add firmware/main/power.h firmware/main/power.c firmware/main/app_main.c
git commit -m "firmware: deep sleep con wake su tasto"
```

---

### Task 9: Wi-Fi (connessione con creds da secrets.h)

Connetti il device alla rete di casa (prerequisito dell'OTA).

**Files:**
- Create: `firmware/main/wifi.h`
- Create: `firmware/main/wifi.c`
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`
- Prereq: `firmware/main/secrets.h` (copia da `secrets.h.example`, gitignored)

**Interfaces:**
- Consumes: `secrets.h` (`WIFI_SSID`, `WIFI_PASSWORD`).
- Produces:
  - `esp_err_t wifi_connect(int timeout_ms);` (init STA, connette, aspetta l'IP)
  - `esp_err_t wifi_get_ip(char *out, size_t len);`

- [ ] **Step 1: Crea `secrets.h`** (non committato)

```bash
cp firmware/main/secrets.h.example firmware/main/secrets.h
# poi modifica firmware/main/secrets.h con SSID/password reali della rete di casa
```

- [ ] **Step 2: `firmware/main/wifi.h`**

```c
#pragma once
#include "esp_err.h"
#include <stddef.h>

esp_err_t wifi_connect(int timeout_ms);
esp_err_t wifi_get_ip(char *out, size_t len);
```

- [ ] **Step 3: `firmware/main/wifi.c`**

Inizializza NVS, netif, event loop; STA con `WIFI_SSID`/`WIFI_PASSWORD` da `secrets.h`;
usa un `EventGroup` per attendere `IP_EVENT_STA_GOT_IP` fino a `timeout_ms`; salva l'IP
per `wifi_get_ip`. Ritorna `ESP_OK` all'ottenimento dell'IP, `ESP_ERR_TIMEOUT` altrimenti.

- [ ] **Step 4: Registra il sorgente** in `firmware/main/CMakeLists.txt` (aggiungi `wifi.c`).

- [ ] **Step 5: Connetti in `app_main.c`** (prima del ramo deep sleep)

```c
#include "wifi.h"
    if (wifi_connect(15000) == ESP_OK) {
        char ip[16]; wifi_get_ip(ip, sizeof(ip));
        ESP_LOGI(TAG, "wifi ok, ip=%s", ip);
        display_text("wifi ok", ip);
    } else {
        ESP_LOGW(TAG, "wifi failed");
    }
```

- [ ] **Step 6: Verifica (hardware-in-the-loop)**

Run: `idf.py -p /dev/cu.usbmodem1101 flash monitor`
Expected: log `wifi ok, ip=192.168.x.y`; stesso IP sull'e-Paper. **Criterio tappa 8 (parte Wi-Fi).**

- [ ] **Step 7: Commit** (senza `secrets.h`)

```bash
git add firmware/main/wifi.h firmware/main/wifi.c firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: connessione Wi-Fi STA con credenziali da secrets.h"
```

---

### Task 10: Logica manifest OTA + semver (unit test su host)

Logica pura, senza hardware: parsing del manifest e confronto versioni. Testata su
host-target Linux con TDD.

**Files:**
- Create: `firmware/main/ota_manifest.h`
- Create: `firmware/main/ota_manifest.c`
- Create: `firmware/host_test/CMakeLists.txt`
- Create: `firmware/host_test/main/CMakeLists.txt`
- Test: `firmware/host_test/main/test_ota_manifest.c`

**Interfaces:**
- Consumes: —
- Produces:
  - `typedef struct { char version[16]; char url[256]; char sha256[65]; } ota_manifest_t;`
  - `bool ota_manifest_parse(const char *json, ota_manifest_t *out);`
  - `int ota_semver_cmp(const char *a, const char *b);` (−1/0/1)
  - `bool ota_should_update(const char *current, const ota_manifest_t *m);`

- [ ] **Step 1: Scrivi il test che fallisce** (`firmware/host_test/main/test_ota_manifest.c`)

```c
#include "unity.h"
#include "ota_manifest.h"
#include <string.h>

void test_semver_cmp(void) {
    TEST_ASSERT_EQUAL_INT(-1, ota_semver_cmp("0.1.0", "0.2.0"));
    TEST_ASSERT_EQUAL_INT( 1, ota_semver_cmp("1.0.0", "0.9.9"));
    TEST_ASSERT_EQUAL_INT( 0, ota_semver_cmp("1.2.3", "1.2.3"));
}

void test_parse_valid(void) {
    const char *j = "{\"version\":\"0.2.0\",\"url\":\"http://h/f.bin\",\"sha256\":\"abc\"}";
    ota_manifest_t m;
    TEST_ASSERT_TRUE(ota_manifest_parse(j, &m));
    TEST_ASSERT_EQUAL_STRING("0.2.0", m.version);
    TEST_ASSERT_EQUAL_STRING("http://h/f.bin", m.url);
    TEST_ASSERT_EQUAL_STRING("abc", m.sha256);
}

void test_parse_malformed_returns_false(void) {
    ota_manifest_t m;
    TEST_ASSERT_FALSE(ota_manifest_parse("not json", &m));
}

void test_should_update(void) {
    ota_manifest_t m; strcpy(m.version, "0.2.0");
    TEST_ASSERT_TRUE(ota_should_update("0.1.0", &m));
    TEST_ASSERT_FALSE(ota_should_update("0.2.0", &m)); // uguale
    TEST_ASSERT_FALSE(ota_should_update("0.3.0", &m)); // piu' vecchio nel manifest
}

void app_main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_semver_cmp);
    RUN_TEST(test_parse_valid);
    RUN_TEST(test_parse_malformed_returns_false);
    RUN_TEST(test_should_update);
    UNITY_END();
}
```

- [ ] **Step 2: `firmware/host_test/CMakeLists.txt`**

```cmake
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(host_test)
```

- [ ] **Step 3: `firmware/host_test/main/CMakeLists.txt`** (riusa il sorgente in `../../main`)

```cmake
idf_component_register(
    SRCS "test_ota_manifest.c" "../../main/ota_manifest.c"
    INCLUDE_DIRS "../../main"
    REQUIRES unity
)
```

- [ ] **Step 4: Verifica che fallisca (host)**

Run: `cd firmware/host_test && idf.py --preview set-target linux && idf.py build`
Expected: FAIL di compilazione/link (`ota_manifest_*` non definiti).

- [ ] **Step 5: `firmware/main/ota_manifest.h`**

```c
#pragma once
#include <stdbool.h>

typedef struct {
    char version[16];
    char url[256];
    char sha256[65];
} ota_manifest_t;

bool ota_manifest_parse(const char *json, ota_manifest_t *out);
int  ota_semver_cmp(const char *a, const char *b);
bool ota_should_update(const char *current, const ota_manifest_t *m);
```

- [ ] **Step 6: `firmware/main/ota_manifest.c`**

Parsing con cJSON (in-tree in ESP-IDF): estrai `version`/`url`/`sha256` in `out`,
ritorna `false` su JSON invalido o campi mancanti. `ota_semver_cmp`: fai il parse di
`MAJOR.MINOR.PATCH` con `sscanf` e confronta i tre interi. `ota_should_update`:
`ota_semver_cmp(current, m->version) < 0`.

```c
#include "ota_manifest.h"
#include "cJSON.h"
#include <stdio.h>
#include <string.h>

bool ota_manifest_parse(const char *json, ota_manifest_t *out) {
    cJSON *root = cJSON_Parse(json);
    if (!root) return false;
    cJSON *v = cJSON_GetObjectItem(root, "version");
    cJSON *u = cJSON_GetObjectItem(root, "url");
    cJSON *s = cJSON_GetObjectItem(root, "sha256");
    bool ok = cJSON_IsString(v) && cJSON_IsString(u) && cJSON_IsString(s);
    if (ok) {
        snprintf(out->version, sizeof(out->version), "%s", v->valuestring);
        snprintf(out->url, sizeof(out->url), "%s", u->valuestring);
        snprintf(out->sha256, sizeof(out->sha256), "%s", s->valuestring);
    }
    cJSON_Delete(root);
    return ok;
}

int ota_semver_cmp(const char *a, const char *b) {
    int a1=0,a2=0,a3=0,b1=0,b2=0,b3=0;
    sscanf(a, "%d.%d.%d", &a1,&a2,&a3);
    sscanf(b, "%d.%d.%d", &b1,&b2,&b3);
    if (a1!=b1) return a1<b1?-1:1;
    if (a2!=b2) return a2<b2?-1:1;
    if (a3!=b3) return a3<b3?-1:1;
    return 0;
}

bool ota_should_update(const char *current, const ota_manifest_t *m) {
    return ota_semver_cmp(current, m->version) < 0;
}
```

- [ ] **Step 7: Verifica pass (host)**

Run: `cd firmware/host_test && idf.py build && ./build/host_test.elf`
Expected: tutti i test Unity PASS.

- [ ] **Step 8: Commit**

```bash
git add firmware/main/ota_manifest.h firmware/main/ota_manifest.c \
        firmware/host_test/CMakeLists.txt firmware/host_test/main/CMakeLists.txt \
        firmware/host_test/main/test_ota_manifest.c
git commit -m "firmware: parsing manifest OTA e confronto semver con unit test host"
```

---

### Task 11: OTA pull + server firmware di test

Scarica e applica un firmware più nuovo indicato dal manifest; testalo contro un
server statico sul Mac.

**Files:**
- Create: `firmware/main/ota.h`
- Create: `firmware/main/ota.c`
- Create: `firmware/tools/serve_firmware.py`
- Modify: `firmware/main/idf_component.yml` (`esp_https_ota` è in IDF; nessuna dep extra)
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `ota_manifest.h` (Task 10), `wifi.h`, `secrets.h` (`OTA_MANIFEST_URL`), `fw_version.h`.
- Produces:
  - `esp_err_t ota_pull(const char *manifest_url);` (GET manifest → se più nuovo, scarica+verifica sha256+scrive partizione inattiva+reboot)

- [ ] **Step 1: `firmware/tools/serve_firmware.py`**

```python
#!/usr/bin/env python3
"""Server statico per testare l'OTA pull. Serve manifest.json e i .bin.
Uso: cd firmware && python3 tools/serve_firmware.py 0.2.0
Genera manifest.json puntando a build/secondbrain_fw.bin con sha256, poi serve la dir.
"""
import hashlib, http.server, json, socket, socketserver, sys, shutil
from pathlib import Path

def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "0.2.0"
    port = 8000
    root = Path("ota_serve"); root.mkdir(exist_ok=True)
    (root / "firmware").mkdir(exist_ok=True)
    src = Path("build/secondbrain_fw.bin")
    if not src.exists():
        sys.exit("build/secondbrain_fw.bin non trovato: esegui prima `idf.py build`")
    dst = root / "firmware" / f"secondbrain-{version}.bin"
    shutil.copy(src, dst)
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()
    ip = socket.gethostbyname(socket.gethostname())
    manifest = {"version": version,
                "url": f"http://{ip}:{port}/firmware/secondbrain-{version}.bin",
                "sha256": sha}
    (root / "firmware" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"manifest: http://{ip}:{port}/firmware/manifest.json")
    print(json.dumps(manifest, indent=2))
    import os; os.chdir(root)
    with socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler) as h:
        print(f"serving on :{port} (Ctrl-C per uscire)")
        h.serve_forever()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `firmware/main/ota.h`**

```c
#pragma once
#include "esp_err.h"

esp_err_t ota_pull(const char *manifest_url);
```

- [ ] **Step 3: `firmware/main/ota.c`** (parte pull)

`ota_pull`: usa `esp_http_client` per GET del manifest in un buffer, `ota_manifest_parse`,
e se `ota_should_update(fw_version(), &m)` è vero avvia `esp_https_ota` (o
`esp_http_client` + `esp_ota_*`) sull'`url`. Verifica lo **sha256** del `.bin` scaricato
contro `m.sha256` prima di impostare il boot. A download completo e valido, `esp_restart()`.
Se la versione non è più nuova, ritorna `ESP_OK` senza fare nulla.

> Per HTTP in chiaro sulla LAN, abilita `CONFIG_OTA_ALLOW_HTTP`/`skip_cert_common_name_check`
> o usa `esp_http_client` diretto; in produzione (backend) si passerà a HTTPS.

- [ ] **Step 4: Registra il sorgente** in `firmware/main/CMakeLists.txt` (aggiungi `ota.c`, `ota_manifest.c`).

- [ ] **Step 5: Chiama il pull nel flusso normale in `app_main.c`** (dopo wifi ok, prima del deep sleep)

```c
#include "ota.h"
#include "secrets.h"
    if (mode == BOOT_NORMAL) {
        esp_err_t r = ota_pull(OTA_MANIFEST_URL);
        ESP_LOGI(TAG, "ota_pull -> %s", esp_err_to_name(r));
    }
```

- [ ] **Step 6: Verifica end-to-end (hardware-in-the-loop)**

1. Bump `version.txt` a `0.2.0`, `idf.py build`.
2. `python3 tools/serve_firmware.py 0.2.0` sul Mac (annota l'IP; mettilo in `secrets.h` come `OTA_MANIFEST_URL`).
3. Flasha la **0.1.0** corrente, poi resetta: il device vede la 0.2.0, scarica, verifica sha256, riavvia.
Expected: dopo il reboot il log/e-Paper mostra `v0.2.0`; il server logga il GET di manifest e `.bin`. **Criterio tappa 9 (pull).**

- [ ] **Step 7: Commit**

```bash
git add firmware/main/ota.h firmware/main/ota.c firmware/tools/serve_firmware.py \
        firmware/main/CMakeLists.txt firmware/main/app_main.c
git commit -m "firmware: OTA pull da manifest con verifica sha256 e server di test"
```

---

### Task 12: OTA push dev (`POST /ota`) in dev mode

In *dev mode* il device resta sveglio ed espone un endpoint HTTP per ricevere un `.bin`
dal Mac senza cavo.

**Files:**
- Modify: `firmware/main/ota.h`
- Modify: `firmware/main/ota.c`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `wifi.h`, `esp_http_server`, `esp_ota_ops`.
- Produces:
  - `esp_err_t ota_dev_server_start(void);` (avvia `esp_http_server` con `POST /ota` che scrive lo stream nella partizione inattiva e riavvia)

- [ ] **Step 1: Estendi `firmware/main/ota.h`**

```c
esp_err_t ota_dev_server_start(void);
```

- [ ] **Step 2: Estendi `firmware/main/ota.c`** (handler push)

`ota_dev_server_start`: avvia `httpd_start` e registra `POST /ota`. L'handler apre
`esp_ota_begin` sulla partizione `esp_ota_get_next_update_partition`, legge il body a
blocchi con `httpd_req_recv` chiamando `esp_ota_write`, poi `esp_ota_end` +
`esp_ota_set_boot_partition`; risponde `200 OK` e schedula `esp_restart()`.

- [ ] **Step 3: Avvia il server in dev mode in `app_main.c`**

```c
    if (mode == BOOT_DEV) {
        if (wifi_connect(15000) == ESP_OK) {
            char ip[16]; wifi_get_ip(ip, sizeof(ip));
            ESP_ERROR_CHECK(ota_dev_server_start());
            ESP_LOGI(TAG, "DEV OTA ready: curl --data-binary @build/secondbrain_fw.bin http://%s/ota", ip);
            display_text("DEV MODE", ip);
            return; // resta sveglio, non dormire
        }
    }
```

- [ ] **Step 4: Verifica end-to-end (hardware-in-the-loop)**

1. Bump `version.txt` a `0.3.0`, `idf.py build`.
2. Resetta la scheda **tenendo premuto** il tasto (dev mode); annota l'IP.
3. Dal Mac: `curl --data-binary @build/secondbrain_fw.bin http://<device-ip>/ota`.
Expected: il device riceve, scrive, riavvia; dopo il reboot mostra `v0.3.0`. **Criterio tappa 9 (push).**

- [ ] **Step 5: Commit**

```bash
git add firmware/main/ota.h firmware/main/ota.c firmware/main/app_main.c
git commit -m "firmware: OTA push dev via POST /ota in dev mode"
```

---

### Task 13: Rollback confirm + verifica finale

Rendi l'OTA sicuro: conferma la validità dopo un self-check, così un firmware difettoso
viene ripristinato in automatico dal bootloader.

**Files:**
- Modify: `firmware/main/ota.h`
- Modify: `firmware/main/ota.c`
- Modify: `firmware/main/app_main.c`

**Interfaces:**
- Consumes: `esp_ota_ops`, `wifi.h`.
- Produces:
  - `void ota_mark_valid_if_pending(void);` (se l'app è `ESP_OTA_IMG_PENDING_VERIFY` e il self-check passa, chiama `esp_ota_mark_app_valid_cancel_rollback()`)

- [ ] **Step 1: Estendi `firmware/main/ota.h`**

```c
void ota_mark_valid_if_pending(void);
```

- [ ] **Step 2: Estendi `firmware/main/ota.c`**

```c
#include "esp_ota_ops.h"

void ota_mark_valid_if_pending(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(running, &st) == ESP_OK &&
        st == ESP_OTA_IMG_PENDING_VERIFY) {
        // Self-check minimo: siamo arrivati fin qui col boot completo. Conferma.
        esp_ota_mark_app_valid_cancel_rollback();
    }
}
```

- [ ] **Step 3: Chiama il confirm presto in `app_main.c`** (dopo `power_init`, prima delle periferiche pesanti; conferma solo dopo che il boot base è ok)

```c
#include "ota.h"
    // ...dopo aver verificato display + (opzionale) wifi come self-check:
    ota_mark_valid_if_pending();
```

- [ ] **Step 4: Verifica rollback (hardware-in-the-loop)**

1. Crea di proposito un firmware "cattivo" (es. `abort();` in cima ad `app_main`), bump versione, servilo via pull o push.
2. Il device lo installa e riavvia; il firmware cattivo va in crash prima del confirm.
Expected: al boot successivo il bootloader **ripristina** la versione precedente valida (log `rollback`), e il device torna funzionante. Ripristina poi `app_main` normale. **Criterio tappa 9 (rollback).**

- [ ] **Step 5: Verifica completa Fase 0**

Ripercorri i criteri delle tappe 1–9 (sezione 5 della spec) una volta di fila su un
device appena flashato: display, tasti/boot mode, SD, audio→WAV, sensori, deep sleep,
Wi-Fi, OTA pull, OTA push, rollback. Tutti devono passare.

- [ ] **Step 6: Commit**

```bash
git add firmware/main/ota.h firmware/main/ota.c firmware/main/app_main.c
git commit -m "firmware: rollback confirm OTA dopo self-check"
```

---

## Note di integrazione (post-piano)

- **Contratto manifest** (riusato dal backend in Fase 1): `{version, url, sha256}`.
  L'endpoint `/firmware/manifest.json` del backend dovrà servire lo stesso schema, così
  il device non cambia passando dal server statico al backend reale.
- **Da HTTP a HTTPS:** in produzione col backend, l'OTA pull passerà a HTTPS (certificato
  del backend). Il server statico di sviluppo resta in HTTP sulla LAN.
- **Refresh parziale e-Paper, provisioning Wi-Fi (NVS/BLE), secure boot/flash encryption:**
  ottimizzazioni/hardening rimandati a fasi successive (fuori scope Fase 0).

## Self-Review

- **Copertura spec:** toolchain+hello (Task 1) ✓; pin-map/partizioni/versione (Task 2) ✓;
  e-Paper (Task 3) ✓; tasti+boot mode (Task 4) ✓; SD (Task 5) ✓; audio→WAV (Task 6) ✓;
  sensori (Task 7) ✓; deep sleep (Task 8) ✓; Wi-Fi (Task 9) ✓; manifest+semver host-test
  (Task 10) ✓; OTA pull + server statico (Task 11) ✓; OTA push dev (Task 12) ✓; rollback
  confirm + verifica finale (Task 13) ✓. Tutte le 9 tappe di bring-up + i 3 modelli OTA
  (pull/push/rollback) della sezione 5–6 della spec sono coperti.
- **Type consistency:** `ota_manifest_t`, `ota_manifest_parse`, `ota_semver_cmp`,
  `ota_should_update` (Task 10) usati in `ota_pull` (Task 11); `boot_mode_t`/`power_*`
  (Task 4) usati nel deep sleep (Task 8) e nella scelta dev mode (Task 12); `wifi_connect`/
  `wifi_get_ip` (Task 9) usati in Task 11–12; `fw_version()` (Task 2) usato in Task 3/11.
- **Placeholder:** i `-1`/commenti in `board.h` sono valori da riempire dal riferimento
  Waveshare (Task 2 Step 1), non TODO di piano: la centralizzazione dei pin è il design.
  I moduli driver dipendenti dalla scheda (display/audio/SD/sensori) indicano le API
  ESP-IDF esatte e i pin via macro `BOARD_*`; nessun passo lascia il "come" indefinito.
```
