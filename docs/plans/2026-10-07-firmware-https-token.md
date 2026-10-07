# Firmware — HTTPS e token verso il backend — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Il device carica le registrazioni e fa l'OTA pull in HTTPS con il token per dispositivo del backend, e un token sbagliato non svuota mai la coda.

**Architecture:** Un solo URL del server e il token in `secrets.h` (compile time, come le reti Wi-Fi). Un modulo `http_client` prepara la configurazione di `esp_http_client` (bundle di certificati per `https://`, header `Authorization: Bearer`) per i tre punti che parlano col backend: upload (`sync.c`), manifest e binario (`ota.c`). La politica degli esiti HTTP resta una funzione pura (`sync_policy.c`) con test host: `401`/`403` fermano il sync e segnalano "token ko", `429` ferma come i `5xx`.

**Tech Stack:** ESP-IDF v5.3.1 (esp32s3), `esp_http_client` con `esp_crt_bundle`, unity su host (target linux), backend FastAPI in Docker sul Mac, `cloudflared` (quick tunnel) per la verifica HTTPS.

**Spec:** `docs/specs/2026-10-07-firmware-https-token-design.md` (leggerla prima: i §x citati sotto sono i suoi).

## Global Constraints

- Prima di ogni `idf.py`: `. ~/esp/esp-idf/export.sh`. Firmware in `firmware/` (target esp32s3), test host in `firmware/host_test/` (target linux, già configurato).
- Test host: `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf`. L'eseguibile **non termina da solo**: leggere il riepilogo unity (`30 Tests 0 Failures` oggi) e lasciare che `timeout` lo chiuda. Tutti i test devono restare verdi.
- Build firmware: `cd firmware && idf.py build`. Zero warning (`-Werror` su format-truncation).
- Device con sola alimentazione USB: niente seriale. Build di prova via **OTA push** con il device in DEV MODE (PWR, rilascio, USER entro 1 s; l'IP compare sul display): `curl --data-binary @build/secondbrain_fw.bin http://<ip>/ota`. Diagnostica con `GET http://<ip>/status` in DEV MODE.
- `version.txt` in git resta `0.1.0`; per le build di prova si bumpa in locale (`0.7.0`, `0.7.1`) e si ripristina prima del commit. Dopo i test, sul backend deve restare pubblicata la versione installata sul device (trappola OTA, vedi `CLAUDE.md`).
- `main/secrets.h` è gitignored: **mai committarlo**. `secrets.h.example` sì.
- Buffer ≥ 1 KB sull'heap; `esp_err_t` di ritorno; nessun `ESP_ERROR_CHECK` nel flusso normale; costanti in `main/config.h`.
- Commit piccoli, in italiano, prefisso `firmware:` o `docs:`; corpo con il perché e cosa è stato verificato sull'hardware; **nessun riferimento all'assistente**; autore `francecesco <francecesco78@gmail.com>` (config locale del repo).
- Backend sul Mac: `cd backend && docker compose up -d`, porta 8000, `.env` con `ALLOW_UNAUTHENTICATED_LAN=true`. IP del Mac in ufficio il 2026-10-07: `192.168.0.194` (cambia col DHCP: `ipconfig getifaddr en0`).

## Review Focus

1. Token con spazi o a capo copiato male in `secrets.h` → il backend risponde `401`, il file resta in coda e il display dice `token ko` (Task 1 e scenario 1 del Task 4): mai `rejected/`.
2. `429` da Cloudflare (Bot Fight Mode) su un upload → file conservato e sync interrotto, non rifiutato (test host nel Task 1).
3. URL `http://` dopo l'introduzione del bundle → la configurazione con `crt_bundle_attach` non deve rompere il caso in chiaro (scenario 4 del Task 4).
4. Manifest con `401` dal tunnel → il pull salta con un log chiaro e il ciclo finisce in deep sleep come oggi (Task 3, scenario 1 del Task 4: l'OTA viene dopo il sync nello stesso ciclo).
5. Heap insufficiente per il TLS dopo una cattura lunga → `esp_http_client_open` fallisce con `ESP_ERR_NO_MEM`, lo status resta `0`, il file resta in coda; `free_heap_min` in `/status` lo rende visibile (Task 3, scenario 5 del Task 4).

---

## File Structure

- `firmware/main/sync_policy.h/.c` (modifica): nuova azione `SYNC_ACTION_STOP_AUTH`.
- `firmware/main/sync.h/.c` (modifica): `auth_error` nel risultato; URL da `SERVER_BASE_URL`; client HTTP dal modulo comune; heap minimo in `diag`.
- `firmware/main/app_main.c` (modifica): `auth_error` nello stato del ciclo e riga 4 `token ko`; URL del manifest da `SERVER_BASE_URL`.
- `firmware/main/wifi_select.h` (modifica): `wifi_network_t` senza `server`. `firmware/main/wifi.h/.c`: via `wifi_server_base_url()`.
- `firmware/main/secrets.h.example` (modifica), `firmware/main/secrets.h` (locale, non in git).
- `firmware/main/http_client.h/.c` (nuovo): configurazione comune del client HTTP, header del token, classificazione degli status di autorizzazione.
- `firmware/main/ota.c` (modifica): manifest e download con il modulo comune; log "token rifiutato".
- `firmware/main/diag.h`, `firmware/main/status_http.c` (modifica): `last_http`, `free_heap_min`.
- `firmware/main/CMakeLists.txt` (modifica): `http_client.c`.
- `firmware/host_test/main/test_policies.c`, `test_wifi_select.c` (modifica).
- `README.md` (radice, sezione firmware), `backend/README.md`, `CLAUDE.md`, spec (Stato).

---

### Task 1: Politica degli esiti — `401`/`403` fermano il sync, `429` come i `5xx` (host-test)

**Files:**
- Modify: `firmware/main/sync_policy.h`, `firmware/main/sync_policy.c`
- Modify: `firmware/main/sync.h`, `firmware/main/sync.c:120-130`
- Modify: `firmware/main/app_main.c:43-49` (cycle_state), `:205-209` (copia del risultato), `:247-252` (riga 4)
- Test: `firmware/host_test/main/test_policies.c`

**Interfaces:**
- Produces: `SYNC_ACTION_STOP_AUTH` in `sync_action_t`; `bool auth_error` in `sync_result_t`.

- [ ] **Step 1: Test host che fallisce**

In `firmware/host_test/main/test_policies.c`, sostituire `test_sync_decide` con:

```c
void test_sync_decide(void) {
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(200));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(201));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(409));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(400));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(413));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(404));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(422));
    // Token assente/sbagliato o device diverso dal token: non e' colpa del file,
    // la coda resta intatta e il sync si ferma (spec §6).
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP_AUTH, sync_decide(401));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP_AUTH, sync_decide(403));
    // Rate limit (Cloudflare): temporaneo come un 5xx.
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(429));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(500));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(503));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(0));   // errore rete/timeout
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(-1));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(302)); // redirect: non gestito -> stop
}
```

- [ ] **Step 2: Verificare che fallisca**

Run: `cd firmware/host_test && idf.py build 2>&1 | tail -3`
Expected: errore di compilazione, `SYNC_ACTION_STOP_AUTH` non dichiarato.

- [ ] **Step 3: Implementazione**

`firmware/main/sync_policy.h`:

```c
#pragma once
// Codice HTTP della POST /captures -> azione sul file in coda (spec HTTPS+token §6). Pura.
typedef enum {
    SYNC_ACTION_DELETE,    // 200/201 accettata, 409 duplicato
    SYNC_ACTION_REJECT,    // altri 4xx: sposta in rejected/, prosegui
    SYNC_ACTION_STOP,      // 429, 5xx, redirect, errore rete (status <= 0): tieni, interrompi il sync
    SYNC_ACTION_STOP_AUTH, // 401/403: token assente, sbagliato o di un altro device: tieni, interrompi, segnala
} sync_action_t;
sync_action_t sync_decide(int http_status);
```

`firmware/main/sync_policy.c`:

```c
#include "sync_policy.h"

sync_action_t sync_decide(int http_status)
{
    if (http_status == 200 || http_status == 201 || http_status == 409) return SYNC_ACTION_DELETE;
    if (http_status == 401 || http_status == 403) return SYNC_ACTION_STOP_AUTH;
    if (http_status == 429) return SYNC_ACTION_STOP;
    if (http_status >= 400 && http_status < 500) return SYNC_ACTION_REJECT;
    return SYNC_ACTION_STOP;
}
```

- [ ] **Step 4: Test host verdi**

Run: `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf 2>&1 | tail -4`
Expected: `30 Tests 0 Failures 0 Ignored`.

- [ ] **Step 5: Risultato del sync e display**

`firmware/main/sync.h`, struct:

```c
typedef struct {
    int  sent;          // file accettati (200/201/409) e cancellati
    int  rejected;      // file spostati in rejected/ (altri 4xx)
    int  remaining;     // file ancora in coda alla fine
    bool server_error;  // sync interrotto per 429/5xx/timeout/rete
    bool auth_error;    // sync interrotto per 401/403: token da sistemare
} sync_result_t;
```

`firmware/main/sync.c`, lo `switch` in `sync_run`:

```c
        switch (sync_decide(status)) {
        case SYNC_ACTION_DELETE: queue_delete(names[i]); out->sent++; break;
        case SYNC_ACTION_REJECT: queue_reject(names[i]); out->rejected++; ESP_LOGW(TAG, "%s rifiutato (HTTP %d)", names[i], status); break;
        case SYNC_ACTION_STOP:   out->server_error = true; ESP_LOGW(TAG, "sync interrotto (HTTP %d)", status); i = n; break;
        case SYNC_ACTION_STOP_AUTH:
            out->auth_error = true;
            ESP_LOGE(TAG, "token rifiutato dal server (HTTP %d): coda conservata, controlla DEVICE_TOKEN", status);
            i = n; break;
        }
```

e il ritorno: `return (out->server_error || out->auth_error) ? ESP_FAIL : ESP_OK;`. Nel log finale aggiungere `auth_error=%d`.

`firmware/main/app_main.c`: in `cycle_state_t` aggiungere `bool auth_error;` dopo `server_error`; in `do_sync` copiare anche `st->auth_error = r.auth_error;`; in `show_status` inserire prima del ramo `server_error`:

```c
    else if (st->auth_error) strlcpy(l4, "token ko", sizeof(l4));
```

- [ ] **Step 6: Build firmware senza warning**

Run: `cd firmware && idf.py build 2>&1 | grep -E "warning|error|Project build complete" | tail -3`
Expected: solo `Project build complete`.

- [ ] **Step 7: Commit**

```bash
git add firmware/main/sync_policy.h firmware/main/sync_policy.c firmware/main/sync.h firmware/main/sync.c firmware/main/app_main.c firmware/host_test/main/test_policies.c
git commit -m "firmware: 401/403 fermano il sync senza toccare la coda, 429 come i 5xx" -m "Con il token in arrivo, un errore di configurazione mandava l'intera coda in rejected/ al primo ciclo: ora 401 e 403 conservano i file, interrompono il sync e mostrano \"token ko\" sul display; 429 (rate limit di Cloudflare) e' temporaneo come un 5xx. Test host: 30 verdi."
```

---

### Task 2: Un solo URL del server e token in `secrets.h`

**Files:**
- Modify: `firmware/main/wifi_select.h:7-12`, `firmware/main/wifi.h:15-17`, `firmware/main/wifi.c:190-194`
- Modify: `firmware/main/secrets.h.example`, `firmware/main/secrets.h` (locale)
- Modify: `firmware/main/sync.c:41-44`, `firmware/main/app_main.c:201`, `:212-213`
- Modify: `README.md:119-132`
- Test: `firmware/host_test/main/test_wifi_select.c:13-16`

**Interfaces:**
- Produces: `SERVER_BASE_URL` e `DEVICE_TOKEN` (stringhe) da `secrets.h`; `wifi_network_t` a tre campi `{ssid, password, bssid}`.

- [ ] **Step 1: Test host aggiornato (deve fallire a compilare finché la struct ha quattro campi? No: C accetta inizializzatori parziali. Il test cambia per coerenza, la build resta verde)**

In `firmware/host_test/main/test_wifi_select.c`:

```c
static const wifi_network_t NETS[] = {
    { "Casa",    "pw1", "54:78:f0:bd:65:eb" },
    { "Ufficio", "pw2", NULL },
};
```

- [ ] **Step 2: Struct e API**

`firmware/main/wifi_select.h`, la struct:

```c
typedef struct {
    const char *ssid;
    const char *password;
    const char *bssid;    // "aa:bb:cc:dd:ee:ff" per forzare un AP, oppure NULL
} wifi_network_t;
```

`firmware/main/wifi.h`: togliere la dichiarazione e il commento di `wifi_server_base_url`. `firmware/main/wifi.c`: togliere la funzione (righe 190-194). Aggiornare il commento in testa a `wifi.h` se cita il server.

- [ ] **Step 3: `secrets.h.example`**

```c
#pragma once
// Copia questo file in secrets.h (gitignored) e compila i valori reali.
//
// Reti Wi-Fi note. Il device fa una scansione e si collega alla rete configurata con il
// segnale migliore tra quelle presenti. Per ogni voce: SSID, password, BSSID da forzare
// ("aa:bb:cc:dd:ee:ff", oppure NULL per qualunque AP: utile se piu' AP condividono lo
// stesso SSID e uno non inoltra TCP).
#define WIFI_NETWORKS { \
    { "casa-ssid",    "password-casa",    "54:78:f0:bd:65:eb" }, \
    { "ufficio-ssid", "password-ufficio", NULL }, \
}

// Base URL del backend, lo stesso su tutte le reti: https://ingest.<dominio> dietro il
// tunnel Cloudflare, oppure http://<ip>:8000 per un backend in LAN. Da qui derivano
// POST /captures e GET /firmware/manifest.json (OTA). Niente barra finale.
#define SERVER_BASE_URL "http://192.168.1.50:8000"

// Token del dispositivo, stampato una volta sola da `secondbrain device add <mac>` o
// `secondbrain device token <mac>` sul backend. Vuoto: nessun header Authorization
// (funziona solo con ALLOW_UNAUTHENTICATED_LAN=true sul backend e device in LAN).
#define DEVICE_TOKEN ""
```

Aggiornare `firmware/main/secrets.h` (locale) allo stesso schema: le due reti a tre campi, `SERVER_BASE_URL "http://192.168.0.194:8000"` (Mac in ufficio), `DEVICE_TOKEN ""` per ora.

- [ ] **Step 4: Chiamanti**

`firmware/main/sync.c`, in `upload_one` sostituire le righe `const char *base = ...; if (!base) ...; snprintf(url, ..., "%s/captures", base);` con:

```c
    char url[192];
    snprintf(url, sizeof(url), "%s/captures", SERVER_BASE_URL);
```

e aggiungere `#include "secrets.h"` tra gli include. `firmware/main/app_main.c`: `#include "secrets.h"`; il log diventa `ESP_LOGI(TAG, "rete: %s, server: %s", wifi_current_ssid(), SERVER_BASE_URL);` e il manifest `snprintf(manifest_url, sizeof(manifest_url), "%s/firmware/manifest.json", SERVER_BASE_URL);`.

- [ ] **Step 5: Build host e firmware**

Run: `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf 2>&1 | tail -2`
Expected: `30 Tests 0 Failures`.
Run: `cd firmware && idf.py build 2>&1 | grep -E "warning|error|Project build complete" | tail -3`
Expected: solo `Project build complete`.

- [ ] **Step 6: README (radice), sezione "Configurazione"**

Sostituire il blocco di codice e il paragrafo "Il quarto campo..." con:

```c
#define WIFI_NETWORKS { \
    { "ssid-casa",    "password", "aa:bb:cc:dd:ee:ff" }, \
    { "ssid-ufficio", "password", NULL }, \
}
#define SERVER_BASE_URL "https://ingest.esempio.it"   // o http://192.168.1.28:8000 in LAN
#define DEVICE_TOKEN    "<token di secondbrain device add>"  // "" = senza token (solo LAN)
```

e il testo: "Il terzo campo di ogni rete è il BSSID da forzare, oppure `NULL`. Il server è
uno solo per tutte le reti (`SERVER_BASE_URL`, `http://` o `https://`); il token è quello
stampato da `secondbrain device add <mac>` sul backend (vedi `backend/README.md`)."
Nella sezione sul Wi-Fi (riga ~81) togliere "Ogni rete ha il proprio server."

- [ ] **Step 7: Commit**

```bash
git add firmware/main/wifi_select.h firmware/main/wifi.h firmware/main/wifi.c firmware/main/secrets.h.example firmware/main/sync.c firmware/main/app_main.c firmware/host_test/main/test_wifi_select.c README.md
git commit -m "firmware: un solo URL del server e token del device in secrets.h" -m "Con il backend dietro il tunnel l'URL e' lo stesso da ogni rete: il campo server per rete sparisce da WIFI_NETWORKS e arrivano SERVER_BASE_URL e DEVICE_TOKEN (vuoto = senza header, per la LAN senza token). Build e test host verdi."
```

---

### Task 3: Modulo `http_client` — bundle di certificati, header del token, diagnostica

**Files:**
- Create: `firmware/main/http_client.h`, `firmware/main/http_client.c`
- Modify: `firmware/main/CMakeLists.txt` (aggiungere `"http_client.c"` a `SRCS`)
- Modify: `firmware/main/sync.c:45-47` (config), `:63-66` (open), `:76` (status)
- Modify: `firmware/main/ota.c:58-61`, `:81-86`, `:189-192`, `:208-213`
- Modify: `firmware/main/diag.h`, `firmware/main/status_http.c:61-62`

**Interfaces:**
- Consumes: `SERVER_BASE_URL`, `DEVICE_TOKEN` (Task 2).
- Produces:
  - `esp_http_client_config_t http_client_config(const char *url, esp_http_client_method_t method, int timeout_ms);`
  - `void http_client_set_auth(esp_http_client_handle_t client);`
  - `bool http_status_is_auth_error(int status);`
  - `diag_t.last_http` (`int32_t`), `diag_t.free_heap_min` (`uint32_t`), `void diag_note_heap(void);`

- [ ] **Step 1: Header**

`firmware/main/http_client.h`:

```c
#pragma once
#include <stdbool.h>
#include "esp_http_client.h"
// Configurazione comune dei client HTTP verso il backend (spec HTTPS+token §5):
// bundle di certificati di ESP-IDF per gli URL https:// (ignorato per http://),
// header Authorization: Bearer con DEVICE_TOKEN se non e' vuoto.

// Config con url, metodo, timeout di inattivita' e bundle di certificati. Il chiamante
// la passa a esp_http_client_init.
esp_http_client_config_t http_client_config(const char *url, esp_http_client_method_t method,
                                            int timeout_ms);

// Aggiunge "Authorization: Bearer <DEVICE_TOKEN>" se il token non e' vuoto.
void http_client_set_auth(esp_http_client_handle_t client);

// 401 o 403: il server ha rifiutato il token (o il device non corrisponde).
bool http_status_is_auth_error(int status);
```

- [ ] **Step 2: Implementazione**

`firmware/main/http_client.c`:

```c
#include "http_client.h"
#include "secrets.h"
#include <string.h>
#include "esp_crt_bundle.h"
#include "esp_log.h"

static const char *TAG = "http";

esp_http_client_config_t http_client_config(const char *url, esp_http_client_method_t method,
                                            int timeout_ms)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .method = method,
        .timeout_ms = timeout_ms,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    return cfg;
}

void http_client_set_auth(esp_http_client_handle_t client)
{
    static const char token[] = DEVICE_TOKEN;
    if (token[0] == '\0') return;
    // "Bearer " + token: il token e' 43 caratteri (token_urlsafe(32)); 128 bastano con margine.
    char value[128];
    int n = snprintf(value, sizeof(value), "Bearer %s", token);
    if (n < 0 || n >= (int)sizeof(value)) { ESP_LOGE(TAG, "DEVICE_TOKEN troppo lungo"); return; }
    esp_http_client_set_header(client, "Authorization", value);
}

bool http_status_is_auth_error(int status)
{
    return status == 401 || status == 403;
}
```

Nota: `static const char token[] = DEVICE_TOKEN;` compila sia con `""` sia con la stringa; il valore non finisce mai nei log.

- [ ] **Step 3: Diagnostica**

`firmware/main/diag.h`, in `diag_t` dopo `peak`:

```c
    int32_t  last_http;         // status HTTP dell'ultimo upload (0 = nessuno/errore rete)
    uint32_t free_heap_min;     // heap libero minimo osservato nel ciclo (byte, 0 = mai letto)
```

e la funzione `void diag_note_heap(void);  // aggiorna free_heap_min con l'heap libero attuale`. In `firmware/main/diag.c`:

```c
#include "esp_heap_caps.h"
...
void diag_note_heap(void)
{
    uint32_t now = (uint32_t)esp_get_free_heap_size();
    if (s_diag.free_heap_min == 0 || now < s_diag.free_heap_min) s_diag.free_heap_min = now;
}
```

(`esp_get_free_heap_size` sta in `esp_system.h`: includerlo.) In `firmware/main/status_http.c`, dopo `upload_ms`:

```c
    cJSON_AddNumberToObject(lc, "last_http", d->last_http);
    cJSON_AddNumberToObject(lc, "free_heap_min", d->free_heap_min);
```

- [ ] **Step 4: Upload (`sync.c`)**

In `upload_one`: `#include "http_client.h"`; sostituire la riga `esp_http_client_config_t cfg = { .url = url, .method = HTTP_METHOD_POST, .timeout_ms = SB_HTTP_IDLE_TIMEOUT_MS };` con `esp_http_client_config_t cfg = http_client_config(url, HTTP_METHOD_POST, SB_HTTP_IDLE_TIMEOUT_MS);`; dopo `esp_http_client_set_header(c, "Content-Type", "audio/wav");` aggiungere `http_client_set_auth(c);`. Dopo `status = esp_http_client_get_status_code(c);` aggiungere `diag_get()->last_http = status;`. Prima di `esp_http_client_open` e subito dopo `fetch_headers` chiamare `diag_note_heap();` e loggare: `ESP_LOGI(TAG, "heap libero: %lu", (unsigned long)esp_get_free_heap_size());` (include `esp_system.h`). Nel caso `open` fallito il log esiste già e `status` resta `0`.

- [ ] **Step 5: Manifest e binario (`ota.c`)**

`#include "http_client.h"`. In `http_get_to_buffer`: la config diventa `esp_http_client_config_t config = http_client_config(url, HTTP_METHOD_GET, 10000);` e dopo `esp_http_client_init` (se non NULL) `http_client_set_auth(client);`. Nel ramo `status != 200`:

```c
    if (status != 200) {
        if (http_status_is_auth_error(status)) ESP_LOGE(TAG, "manifest: token rifiutato (HTTP %d), controlla DEVICE_TOKEN", status);
        else ESP_LOGE(TAG, "manifest GET status %d", status);
```

In `ota_download_and_apply`: config `http_client_config(m->url, HTTP_METHOD_GET, 15000)`, `http_client_set_auth(client)` dopo l'init, stesso trattamento del log nel ramo `status != 200`. Dopo il download completo: `diag_note_heap();`.

- [ ] **Step 6: Build**

Run: `cd firmware && idf.py build 2>&1 | grep -E "warning|error|Project build complete" | tail -3`
Expected: solo `Project build complete`. Dimensione: `ls -la build/secondbrain_fw.bin` (atteso < 1,3 MB, partizione da 3 MB).

- [ ] **Step 7: Commit**

```bash
git add firmware/main/http_client.h firmware/main/http_client.c firmware/main/CMakeLists.txt firmware/main/sync.c firmware/main/ota.c firmware/main/diag.h firmware/main/diag.c firmware/main/status_http.c
git commit -m "firmware: HTTPS col bundle di certificati e token Bearer verso il backend" -m "Un solo modulo prepara i client HTTP di upload, manifest e download: crt_bundle_attach per gli URL https:// e header Authorization con DEVICE_TOKEN se non vuoto. /status espone last_http e free_heap_min per leggere l'esito e il costo del TLS senza seriale. Verifica sull'hardware nel task successivo."
```

---

### Task 4: Verifica sull'hardware via Wi-Fi (quick tunnel Cloudflare)

**Files:**
- Modify (locale, non committati): `firmware/version.txt`, `firmware/main/secrets.h`
- Modify se emergono bug: i file dei task precedenti (fix nel commit con il perché)

**Interfaces:**
- Consumes: tutto quanto sopra; backend sul Mac; `secondbrain device token`, `secondbrain firmware publish/rollback/list`.

- [ ] **Step 1: Strumenti e backend**

```bash
brew install cloudflared
cd backend && docker compose up -d && docker compose exec app secondbrain device list
cloudflared tunnel --url http://localhost:8000   # in un terminale a parte: stampa https://<x>.trycloudflare.com
curl -s -o /dev/null -w '%{http_code}\n' https://<x>.trycloudflare.com/login   # atteso 200
```

- [ ] **Step 2: Build di prova con token sbagliato (scenario 1)**

`firmware/main/secrets.h`: `SERVER_BASE_URL "https://<x>.trycloudflare.com"`, `DEVICE_TOKEN "token-sbagliato-di-prova"`. `firmware/version.txt`: `0.7.0`. `idf.py build`. Device in DEV MODE (IP sul display), push:

```bash
curl --data-binary @firmware/build/secondbrain_fw.bin http://<ip-device>/ota
```

Poi PWR per uscire dal DEV, una registrazione di qualche secondo tenendo PWR. Atteso: display riga 4 `token ko`, riga 3 `coda: 1`; nel log del backend (`docker compose logs app --since 5m`) una `POST /captures` → `401` con `Cf-Connecting-Ip` dell'ufficio; GET manifest → `401`. Device di nuovo in DEV: `curl http://<ip-device>/status` → `queue` con un file, `last_cycle.last_http: 401`, `free_heap_min` > 0.

- [ ] **Step 3: Token giusto (scenario 2)**

```bash
cd backend && docker compose exec app secondbrain device token 70041dd8263c   # stampa il token
```

`DEVICE_TOKEN` con quel token, `idf.py build`, push come sopra, PWR, poi una pressione breve di PWR (ciclo di solo sync, oppure una nuova registrazione). Atteso: `N inviate` sul display, `201` nel log del backend, la nota nel finder, `device list` con `token=sì` e l'ultimo contatto di oggi. Annotare `free_heap_min` da `/status` e i log dell'heap.

- [ ] **Step 4: OTA pull con token dal tunnel (scenario 3)**

`firmware/version.txt`: `0.7.1`, `idf.py build`, poi:

```bash
cd backend && docker compose cp ../firmware/build/secondbrain_fw.bin app:/tmp/fw.bin \
  && docker compose exec app secondbrain firmware publish /tmp/fw.bin --version 0.7.1
```

Una pressione di PWR: a fine sync il device legge il manifest in HTTPS con il token, scarica 1,2 MB dal tunnel, verifica lo sha256 e riavvia; al ciclo dopo il display mostra `v0.7.1`. Atteso nel log del backend: `GET /firmware/manifest.json` → `200`, `GET /firmware/epaper154/...bin` → `200`.

- [ ] **Step 5: LAN in HTTP senza token (scenario 4) e rifiuto definitivo (scenario 6)**

`SERVER_BASE_URL "http://192.168.0.194:8000"`, `DEVICE_TOKEN ""`, `version.txt` `0.7.2`, build, push in DEV. Una registrazione → `201` via LAN come prima. Poi scenario 6: in DEV, copiare un file non WAV nella coda non è possibile senza SD esterna; in alternativa verificare via test host che `422` → `REJECT` (già nel Task 1) e, sul backend, che un upload con corpo non WAV risponda `422`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: audio/wav' -H 'X-Capture-Id: cap_20261007_120000' -H 'X-Device-Id: 70041dd8263c' --data-binary 'non-wav' http://127.0.0.1:8000/captures
```

Atteso `422`. Infine pubblicare la versione installata sul device:

```bash
docker compose cp ../firmware/build/secondbrain_fw.bin app:/tmp/fw.bin && docker compose exec app secondbrain firmware publish /tmp/fw.bin --version 0.7.2
```

- [ ] **Step 6: Ripristino e chiusura**

`firmware/version.txt` → `0.1.0`. `secrets.h` resta sul Mac in HTTP con il token vero (`DEVICE_TOKEN` compilato: con `ALLOW_UNAUTHENTICATED_LAN=true` funziona comunque). Chiudere `cloudflared`. `git status` deve mostrare solo file ignorati o nulla. Se durante la verifica è emerso un bug, il fix va in un commit `firmware:` con il perché e l'esito osservato.

---

### Task 5: Documentazione e stato

**Files:**
- Modify: `backend/README.md:40-42`, `:129-135`
- Modify: `README.md` (sezione firmware: coda/rifiuti riga ~77, OTA riga ~87-91)
- Modify: `docs/specs/2026-10-07-firmware-https-token-design.md` (Stato)
- Modify: `CLAUDE.md` (Stato, Prossima sessione, Storico)

- [ ] **Step 1: `backend/README.md`**

Paragrafo "Il comando `device add`...": "Il comando `device add` stampa il token una sola volta: va in `DEVICE_TOKEN` del `secrets.h` del firmware. `ALLOW_UNAUTHENTICATED_LAN=true` in `.env` accetta upload senza token dalla sola LAN; quando tutti i dispositivi mandano il token può tornare `false`."

Paragrafo "Cosa fa il device con ciascun esito": "`201`/`409` → cancella la sua copia; `401`/`403` → tiene il file, interrompe il sync e mostra `token ko` (token da sistemare in `secrets.h`); `429`, `5xx`, timeout → tiene il file e ritenta al ciclo successivo; qualunque altro `4xx` → sposta il file in `rejected/` (non ritenta, il problema non si risolve da solo)." Togliere la frase sul firmware che "sposterebbe l'intera coda in `rejected/`".

- [ ] **Step 2: `README.md` (radice)**

Riga sui rifiuti: "I rifiuti definitivi del server (`4xx` diversi da `401`/`403`/`429`) finiscono in `queue/rejected/`; un token rifiutato ferma il sync e lascia la coda com'è." Nella voce OTA: "`<SERVER_BASE_URL>/firmware/manifest.json`", e aggiungere la voce: "**HTTPS e token**: gli URL `https://` sono verificati col bundle di certificati di ESP-IDF; ogni richiesta porta `Authorization: Bearer <DEVICE_TOKEN>` se il token è impostato."

- [ ] **Step 3: Stato della spec**

Sostituire la riga `**Stato:**` con: "implementato e **verificato sull'hardware il AAAA-MM-GG** via Wi-Fi (piano `docs/plans/2026-10-07-firmware-https-token.md`): token sbagliato → `401`, coda intatta, `token ko`; token giusto → upload `201` e OTA pull `0.7.0 → 0.7.1` in HTTPS da un quick tunnel di Cloudflare; LAN in HTTP senza token invariata. Heap libero minimo durante il sync HTTPS: NN KB. Deviazioni: ..." (compilare con i numeri osservati).

- [ ] **Step 4: `CLAUDE.md`**

Nello "Stato": firmware sul device `0.7.2` con HTTPS + token, `secrets.h` punta al Mac. In "Prossima sessione": resta il deploy sulla ZimaBoard (punto 1) con, in più, "`secrets.h` → `SERVER_BASE_URL https://ingest.<dominio>` e `DEVICE_TOKEN`, poi `ALLOW_UNAUTHENTICATED_LAN=false`"; togliere i punti 2 e 3. Nello "Storico" una voce "2026-10-07 — Test end-to-end AI e firmware HTTPS + token" con i fatti emersi (Gemini senza testo; heap TLS; quick tunnel).

- [ ] **Step 5: Commit**

```bash
git add backend/README.md README.md docs/specs/2026-10-07-firmware-https-token-design.md CLAUDE.md
git commit -m "docs: README, stato della spec e CLAUDE.md dopo HTTPS + token" -m "Nuova politica degli esiti sul device, configurazione SERVER_BASE_URL/DEVICE_TOKEN, esito della verifica sull'hardware."
```

---

## Self-Review

- **Copertura della spec**: §3.1-3.2 → Task 2; §3.3 e §6 → Task 1; §3.4 e §5 → Task 3; §7 → Task 1 (display) e Task 3 (diag); §8 → Task 2 (README firmware) e Task 5; §9 → Task 1-2 (host) e Task 4 (hardware); §3.5 → Task 4.
- **Placeholder**: nessun TBD; lo "Stato" della spec nel Task 5 va compilato con i numeri osservati nel Task 4 (è l'esito, non un placeholder).
- **Coerenza dei nomi**: `SYNC_ACTION_STOP_AUTH`, `auth_error`, `http_client_config`, `http_client_set_auth`, `http_status_is_auth_error`, `diag_note_heap`, `last_http`, `free_heap_min` usati con gli stessi nomi in tutti i task.
- **Review Focus**: 1 e 2 coperti dal test host del Task 1 e dallo scenario 1; 3 dallo scenario 4; 4 dal Task 3 (log) e scenario 1; 5 dal Task 3 (`free_heap_min`) e scenario 5 (annotazione dell'heap nei passi 2-3 del Task 4).
