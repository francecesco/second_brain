# Firmware — Timer di sync — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Con file in coda, il device si sveglia da solo (15 min, poi raddoppio fino a 4 h) per riprovare l'upload, senza consumare a coda vuota e senza lampeggiare lo schermo se non cambia niente.

**Architecture:** La politica (ritardo, se armare, se aggiornare il display) è pura in `sync_retry_policy.c` con test host. Lo stato che sopravvive al deep sleep (tentativo corrente, ultimo esito mostrato) sta in memoria RTC in `retry_state.c`. `power_deep_sleep` prende il ritardo del timer (0 = solo tasto); `power_boot_mode` riconosce il wake da timer dalla causa di wake e ritorna `BOOT_TIMER_SYNC` senza campionare i tasti. `app_main` collega i pezzi: esito del ciclo → refresh condizionato → arma il timer.

**Tech Stack:** ESP-IDF v5.3.1 (`esp_sleep_enable_timer_wakeup`, `esp_sleep_get_wakeup_cause`, `RTC_DATA_ATTR`), unity su host.

**Spec:** `docs/specs/2026-10-07-firmware-timer-sync-design.md` (i §x citati sono i suoi).

## Global Constraints

- `. ~/esp/esp-idf/export.sh` prima di `idf.py`. Test host: `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf` (oggi 32 verdi, non termina da solo). Build: `cd firmware && idf.py build`, zero warning.
- Device con sola USB: build di prova via OTA pull (`version.txt` locale `0.7.6`..., `docker compose cp` + `secondbrain firmware publish`), ripristinare `0.1.0` prima del commit; a fine lavoro sul backend resta pubblicata la versione installata.
- Costanti in `config.h`; buffer ≥ 1 KB sull'heap; `esp_err_t`; nessun `ESP_ERROR_CHECK` nel flusso normale.
- Commit in italiano con prefisso `firmware:`/`docs:`, nessun riferimento all'assistente, autore `francecesco`.
- Backend sul Mac: `cd backend && docker compose up -d`; per simulare il server giù: `docker compose stop app` (il worker e Postgres restano su); `docker compose start app` per rialzarlo. IP del Mac in ufficio `192.168.0.194`, device `192.168.0.217` in DEV MODE.

## Review Focus

1. Scadenza di ciclo raggiunta con coda non vuota → deep sleep senza timer (solo tasto): un blocco non si ripete ogni 15 min (Task 2, `deadline_cb` chiama `power_deep_sleep(0)`).
2. Batteria sotto il 10 % con coda non vuota → nessun timer (test host `sync_retry_should_arm` nel Task 1).
3. Wake da timer con Wi-Fi assente → esito `NO_WIFI`; se era già mostrato, nessun refresh; il tentativo conta e il ritardo raddoppia (Task 1 e 3).
4. Wake da timer che svuota la coda → refresh con `N inviate`, `attempt` a 0, nessun timer al sonno successivo (Task 3, scenario 1 del Task 4).
5. Memoria RTC azzerata (primo boot, OTA) → `attempt` 0 e `shown` NONE: al più un refresh in più (Task 2, valori iniziali).

---

## File Structure

- `firmware/main/sync_retry_policy.h/.c` (nuovi, puri): `sync_retry_delay_ms`, `sync_retry_should_arm`, `status_outcome_t`, `status_should_refresh`.
- `firmware/main/config.h`: `SB_SYNC_RETRY_MIN_MS`, `SB_SYNC_RETRY_MAX_MS`.
- `firmware/main/retry_state.h/.c` (nuovi): stato in RTC (`attempt`, `shown`).
- `firmware/main/power.h/.c`: `BOOT_TIMER_SYNC`, `power_deep_sleep(uint32_t timer_ms)`.
- `firmware/main/app_main.c`: esito, refresh condizionato, timer.
- `firmware/main/diag.h`, `firmware/main/status_http.c`: `retry_attempt`, `next_wake_s`.
- `firmware/main/CMakeLists.txt`, `firmware/host_test/main/CMakeLists.txt`, `firmware/host_test/main/test_policies.c`, `firmware/host_test/main/test_ota_manifest.c` (runner).
- `README.md` (sezione firmware), spec (Stato), `CLAUDE.md`.

---

### Task 1: Politica pura `sync_retry_policy` (host-test)

**Files:**
- Create: `firmware/main/sync_retry_policy.h`, `firmware/main/sync_retry_policy.c`
- Modify: `firmware/main/config.h` (sezione "Rete e sync"), `firmware/main/CMakeLists.txt` (SRCS), `firmware/host_test/main/CMakeLists.txt` (SRCS), `firmware/host_test/main/test_ota_manifest.c` (runner)
- Test: `firmware/host_test/main/test_policies.c`

**Interfaces:**
- Produces: `uint32_t sync_retry_delay_ms(unsigned attempt)`; `bool sync_retry_should_arm(int remaining, bool on_battery, int battery_pct)`; `typedef enum { STATUS_OUTCOME_NONE, STATUS_OUTCOME_OK, STATUS_OUTCOME_SENT, STATUS_OUTCOME_NO_WIFI, STATUS_OUTCOME_SERVER_KO, STATUS_OUTCOME_AUTH_KO } status_outcome_t`; `bool status_should_refresh(int sent, status_outcome_t outcome, status_outcome_t shown)`; costanti `SB_SYNC_RETRY_MIN_MS`, `SB_SYNC_RETRY_MAX_MS`.

- [ ] **Step 1: Test host che fallisce**

In `firmware/host_test/main/test_policies.c`, dopo `#include "sync_policy.h"` aggiungere `#include "sync_retry_policy.h"` e `#include "config.h"`, e prima di `test_timesync_needed`:

```c
void test_sync_retry_delay_doubles_to_cap(void) {
    TEST_ASSERT_EQUAL_UINT32(15 * 60 * 1000, sync_retry_delay_ms(0));
    TEST_ASSERT_EQUAL_UINT32(30 * 60 * 1000, sync_retry_delay_ms(1));
    TEST_ASSERT_EQUAL_UINT32(60 * 60 * 1000, sync_retry_delay_ms(2));
    TEST_ASSERT_EQUAL_UINT32(4 * 60 * 60 * 1000, sync_retry_delay_ms(4));
    TEST_ASSERT_EQUAL_UINT32(4 * 60 * 60 * 1000, sync_retry_delay_ms(10));
    TEST_ASSERT_EQUAL_UINT32(4 * 60 * 60 * 1000, sync_retry_delay_ms(40)); // niente overflow dello shift
}

void test_sync_retry_should_arm(void) {
    TEST_ASSERT_FALSE(sync_retry_should_arm(0, false, -1));   // coda vuota, USB
    TEST_ASSERT_FALSE(sync_retry_should_arm(0, true, 80));    // coda vuota, batteria
    TEST_ASSERT_TRUE(sync_retry_should_arm(1, false, -1));    // USB: la percentuale non conta
    TEST_ASSERT_TRUE(sync_retry_should_arm(3, true, 50));
    TEST_ASSERT_TRUE(sync_retry_should_arm(1, true, SB_BATTERY_MIN_RECORD_PCT));
    TEST_ASSERT_FALSE(sync_retry_should_arm(1, true, SB_BATTERY_MIN_RECORD_PCT - 1)); // sotto soglia: niente Wi-Fi, niente timer
}

void test_status_should_refresh(void) {
    TEST_ASSERT_TRUE(status_should_refresh(2, STATUS_OUTCOME_SENT, STATUS_OUTCOME_SENT));      // inviati: sempre
    TEST_ASSERT_FALSE(status_should_refresh(0, STATUS_OUTCOME_SERVER_KO, STATUS_OUTCOME_SERVER_KO)); // stesso esito
    TEST_ASSERT_TRUE(status_should_refresh(0, STATUS_OUTCOME_AUTH_KO, STATUS_OUTCOME_SERVER_KO));   // esito diverso
    TEST_ASSERT_TRUE(status_should_refresh(0, STATUS_OUTCOME_NO_WIFI, STATUS_OUTCOME_NONE));        // mai mostrato nulla
    TEST_ASSERT_FALSE(status_should_refresh(0, STATUS_OUTCOME_NO_WIFI, STATUS_OUTCOME_NO_WIFI));
}
```

Nel runner `test_ota_manifest.c`: dichiarare le tre funzioni accanto a `test_http_url_has_base` e aggiungere `RUN_TEST` per ciascuna dopo `RUN_TEST(test_http_url_has_base);`.

- [ ] **Step 2: Verificare che fallisca**

Run: `cd firmware/host_test && idf.py build 2>&1 | grep error: | head -2`
Expected: `sync_retry_policy.h` non trovato.

- [ ] **Step 3: Implementazione**

`firmware/main/config.h`, dopo `SB_SYNC_WINDOW_MS`:

```c
// Timer di sync (spec timer §3-4): con file in coda il device si risveglia da solo.
#define SB_SYNC_RETRY_MIN_MS       (15 * 60 * 1000)      // primo tentativo
#define SB_SYNC_RETRY_MAX_MS       (4 * 60 * 60 * 1000)  // tetto del raddoppio
```

`firmware/main/sync_retry_policy.h`:

```c
#pragma once
#include <stdbool.h>
#include <stdint.h>
// Timer di sync (spec timer §4): quando riprovare, se armare il timer, se aggiornare il
// display dopo un wake da timer. Funzioni pure, testate su host.

// Ritardo del tentativo n (0 = primo): SB_SYNC_RETRY_MIN_MS raddoppiato, tetto SB_SYNC_RETRY_MAX_MS.
uint32_t sync_retry_delay_ms(unsigned attempt);

// Vero se va armato il timer: coda non vuota e, a batteria, percentuale non sotto la soglia
// di sync (SB_BATTERY_MIN_RECORD_PCT). A USB la percentuale non conta.
bool sync_retry_should_arm(int remaining, bool on_battery, int battery_pct);

// Esito del ciclo come lo mostra la riga 4 del display.
typedef enum {
    STATUS_OUTCOME_NONE,       // mai mostrato (memoria RTC azzerata)
    STATUS_OUTCOME_OK,         // "sync ok" / "Salvato"
    STATUS_OUTCOME_SENT,       // "N inviate"
    STATUS_OUTCOME_NO_WIFI,    // "no sync"
    STATUS_OUTCOME_SERVER_KO,  // "server ko"
    STATUS_OUTCOME_AUTH_KO,    // "token ko"
} status_outcome_t;

// Dopo un wake da timer: refresh se sono stati inviati file o l'esito e' cambiato.
bool status_should_refresh(int sent, status_outcome_t outcome, status_outcome_t shown);
```

`firmware/main/sync_retry_policy.c`:

```c
#include "sync_retry_policy.h"
#include "config.h"

uint32_t sync_retry_delay_ms(unsigned attempt)
{
    uint64_t ms = SB_SYNC_RETRY_MIN_MS;
    for (unsigned i = 0; i < attempt && ms < SB_SYNC_RETRY_MAX_MS; i++) ms *= 2;
    return ms > SB_SYNC_RETRY_MAX_MS ? SB_SYNC_RETRY_MAX_MS : (uint32_t)ms;
}

bool sync_retry_should_arm(int remaining, bool on_battery, int battery_pct)
{
    if (remaining <= 0) return false;
    if (on_battery && battery_pct < SB_BATTERY_MIN_RECORD_PCT) return false;
    return true;
}

bool status_should_refresh(int sent, status_outcome_t outcome, status_outcome_t shown)
{
    return sent > 0 || outcome != shown;
}
```

Aggiungere `"sync_retry_policy.c"` a `SRCS` in `firmware/main/CMakeLists.txt` e `"../../main/sync_retry_policy.c"` in `firmware/host_test/main/CMakeLists.txt`.

- [ ] **Step 4: Test host verdi, build firmware**

Run: `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf 2>&1 | grep -E "Tests|FAIL"`
Expected: `35 Tests 0 Failures 0 Ignored`.
Run: `cd firmware && idf.py build 2>&1 | grep -E "warning:|error:|Project build complete"`
Expected: solo `Project build complete`.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/sync_retry_policy.h firmware/main/sync_retry_policy.c firmware/main/config.h firmware/main/CMakeLists.txt firmware/host_test/main/CMakeLists.txt firmware/host_test/main/test_policies.c firmware/host_test/main/test_ota_manifest.c
git commit -m "firmware: politica pura del timer di sync (ritardo, armo, refresh)" -m "Backoff 15 min -> 4 h, timer solo con coda non vuota e batteria sopra la soglia di sync, refresh del display dopo un wake da timer solo se cambia qualcosa. Test host 35 verdi."
```

---

### Task 2: Stato in RTC, wake da timer e modalità `BOOT_TIMER_SYNC`

**Files:**
- Create: `firmware/main/retry_state.h`, `firmware/main/retry_state.c`
- Modify: `firmware/main/power.h:7-12`, `:22-23`; `firmware/main/power.c:70-98` (boot mode), `:112-117` (deadline_cb), `:146-166` (deep sleep); `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/app_main.c:336` (chiamata a `power_deep_sleep`)

**Interfaces:**
- Consumes: `status_outcome_t` (Task 1).
- Produces: `BOOT_TIMER_SYNC` (nome `"TIMER"`); `void power_deep_sleep(uint32_t timer_ms)`; `unsigned retry_state_attempt(void)`, `void retry_state_bump(void)`, `void retry_state_reset(void)`, `status_outcome_t retry_state_shown(void)`, `void retry_state_set_shown(status_outcome_t)`.

- [ ] **Step 1: `retry_state`**

`firmware/main/retry_state.h`:

```c
#pragma once
#include "sync_retry_policy.h"
// Stato del timer di sync che sopravvive al deep sleep (memoria RTC, spec timer §5):
// tentativi consecutivi da timer e ultimo esito mostrato sul display. Si azzera a un
// power-on o dopo un riavvio OTA: costa al piu' un refresh in piu'.
unsigned         retry_state_attempt(void);
void             retry_state_bump(void);      // un altro wake da timer senza svuotare la coda
void             retry_state_reset(void);     // wake da tasto o coda vuota
status_outcome_t retry_state_shown(void);
void             retry_state_set_shown(status_outcome_t outcome);
```

`firmware/main/retry_state.c`:

```c
#include "retry_state.h"
#include "esp_attr.h"

RTC_DATA_ATTR static unsigned         s_attempt = 0;
RTC_DATA_ATTR static status_outcome_t s_shown = STATUS_OUTCOME_NONE;

unsigned         retry_state_attempt(void) { return s_attempt; }
void             retry_state_bump(void) { if (s_attempt < 1000) s_attempt++; }
void             retry_state_reset(void) { s_attempt = 0; }
status_outcome_t retry_state_shown(void) { return s_shown; }
void             retry_state_set_shown(status_outcome_t outcome) { s_shown = outcome; }
```

Aggiungere `"retry_state.c"` a `SRCS` in `firmware/main/CMakeLists.txt`.

- [ ] **Step 2: `power.h`**

Enum e commento:

```c
// Modalita' di boot (spec Fase 1a §4.2, timer §5), decisa dalla causa di wake e dai tasti:
//  - BOOT_TIMER_SYNC: wake dal timer di sync -> solo sync, senza campionare i tasti
//  - BOOT_DEV:        USER premuto entro 1 s -> Wi-Fi + server push/status, resta sveglio
//  - BOOT_CAPTURE:    PWR tenuto per tutti i 300 ms iniziali -> registra subito
//  - BOOT_SYNC_ONLY:  pressione breve di PWR, oppure reset/power-on -> solo sync
typedef enum { BOOT_SYNC_ONLY, BOOT_CAPTURE, BOOT_DEV, BOOT_TIMER_SYNC } boot_mode_t;
```

e la firma: `// Deep sleep con wake EXT1 su BOARD_BTN_PWR e, se timer_ms > 0, anche dal timer. Non ritorna.` `void power_deep_sleep(uint32_t timer_ms);`.

- [ ] **Step 3: `power.c`**

In testa a `power_boot_mode`, prima del campionamento:

```c
    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER) {
        ESP_LOGI(TAG, "boot-mode: wake dal timer di sync -> TIMER");
        return BOOT_TIMER_SYNC;
    }
```

In `power_boot_mode_name` aggiungere `case BOOT_TIMER_SYNC: return "TIMER";`. `deadline_cb`: `power_deep_sleep(0);` con il commento `// senza timer: un ciclo bloccato non va ripetuto da soli`. `power_deep_sleep(uint32_t timer_ms)`: dopo l'EXT1:

```c
    if (timer_ms > 0) {
        esp_sleep_enable_timer_wakeup((uint64_t)timer_ms * 1000ULL);
        ESP_LOGI(TAG, "wake dal timer tra %lu s", (unsigned long)(timer_ms / 1000));
    }
```

e il log finale `"entering deep sleep; press PWR to wake"` resta. In `app_main.c` la chiamata diventa `power_deep_sleep(0);` per ora (Task 3 la completa).

- [ ] **Step 4: Build**

Run: `cd firmware && idf.py build 2>&1 | grep -E "warning:|error:|Project build complete"`
Expected: solo `Project build complete`.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/retry_state.h firmware/main/retry_state.c firmware/main/power.h firmware/main/power.c firmware/main/app_main.c firmware/main/CMakeLists.txt
git commit -m "firmware: wake dal timer di sync e stato in memoria RTC" -m "power_deep_sleep prende il ritardo del timer (0 = solo tasto, usato anche dalla scadenza di ciclo), il wake da timer e' la modalita' BOOT_TIMER_SYNC senza campionamento dei tasti, retry_state tiene tentativi ed esito mostrato oltre il deep sleep. Comportamento invariato finche' app_main non arma il timer (task successivo)."
```

---

### Task 3: `app_main` — esito, refresh condizionato, timer; diagnostica

**Files:**
- Modify: `firmware/main/app_main.c` (`cycle_state_t`, `show_status`, flusso finale), `firmware/main/diag.h`, `firmware/main/status_http.c`

**Interfaces:**
- Consumes: Task 1 e 2.
- Produces: `diag_t.retry_attempt`, `diag_t.next_wake_s`; `/status` → `last_cycle.retry_attempt`, `last_cycle.next_wake_s`.

- [ ] **Step 1: Esito del ciclo**

In `app_main.c` includere `sync_retry_policy.h` e `retry_state.h`. Dopo `show_status` aggiungere:

```c
// Esito della riga 4 come categoria, per decidere se un wake da timer deve aggiornare lo schermo.
static status_outcome_t cycle_outcome(const cycle_state_t *st)
{
    if (!st->wifi_ok) return STATUS_OUTCOME_NO_WIFI;
    if (st->auth_error) return STATUS_OUTCOME_AUTH_KO;
    if (st->server_error) return STATUS_OUTCOME_SERVER_KO;
    if (st->sent > 0) return STATUS_OUTCOME_SENT;
    return STATUS_OUTCOME_OK;
}
```

- [ ] **Step 2: Flusso finale**

Sostituire il blocco da `do_sync(&st);` a `power_deep_sleep(0);` con:

```c
    if (mode == BOOT_TIMER_SYNC) ESP_LOGI(TAG, "wake dal timer: tentativo %u", retry_state_attempt());
    do_sync(&st);
    if (st.sd_ok) st.remaining = queue_count(NULL);

    status_outcome_t outcome = cycle_outcome(&st);
    bool refresh = (mode != BOOT_TIMER_SYNC) || status_should_refresh(st.sent, outcome, retry_state_shown());
    if (refresh) { show_status(&st); retry_state_set_shown(outcome); }
    else ESP_LOGI(TAG, "wake dal timer senza novita': display non aggiornato");

    // Timer di sync (spec timer §3): tentativi contati solo tra wake da timer.
    if (mode == BOOT_TIMER_SYNC && st.remaining > 0) retry_state_bump(); else retry_state_reset();
    const power_state_t *ps = battery_last();
    uint32_t timer_ms = 0;
    if (sync_retry_should_arm(st.remaining, ps->valid && ps->source == POWER_SRC_BATTERY, ps->pct))
        timer_ms = sync_retry_delay_ms(retry_state_attempt());
    diag_get()->retry_attempt = retry_state_attempt();
    diag_get()->next_wake_s = timer_ms / 1000;
    diag_get()->t_cycle_end = diag_now_ms();
    vTaskDelay(pdMS_TO_TICKS(300));
    power_deep_sleep(timer_ms);
```

Nota: `show_status` resta com'è (chiamata anche nei wake da tasto, sempre). Il log delle quattro righe in `show_status` viene stampato solo quando si fa il refresh: per avere comunque l'esito nel log, nel ramo `else` stampare `ESP_LOGI(TAG, "esito: %d, coda: %d", (int)outcome, st.remaining);`.

- [ ] **Step 3: Diagnostica**

`diag.h`, dopo `stack_min`:

```c
    uint32_t retry_attempt;     // tentativi consecutivi da timer (0 = ultimo wake da tasto o coda vuota)
    uint32_t next_wake_s;       // secondi al prossimo wake da timer (0 = non armato)
```

`status_http.c`, dopo `stack_min`:

```c
    cJSON_AddNumberToObject(lc, "retry_attempt", d->retry_attempt);
    cJSON_AddNumberToObject(lc, "next_wake_s", d->next_wake_s);
```

- [ ] **Step 4: Build e test host**

Run: `cd firmware && idf.py build 2>&1 | grep -E "warning:|error:|Project build complete"`
Expected: solo `Project build complete`.
Run: `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf 2>&1 | grep -E "Tests|FAIL"`
Expected: `35 Tests 0 Failures 0 Ignored`.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/app_main.c firmware/main/diag.h firmware/main/status_http.c
git commit -m "firmware: timer di sync armato con coda non vuota, display solo se cambia qualcosa" -m "A fine ciclo, con file in coda e batteria sopra la soglia, il device arma il wake dal timer (15 min, raddoppio fino a 4 h tra wake da timer consecutivi, azzerato dal tasto o dalla coda vuota). Dopo un wake da timer lo schermo si aggiorna solo se sono stati inviati file o l'esito e' cambiato. /status espone retry_attempt e next_wake_s. Verifica sull'hardware nel task successivo."
```

---

### Task 4: Verifica sull'hardware

**Files:**
- Modify (locale, non committati): `firmware/version.txt`

- [ ] **Step 1: Build di prova e installazione**

`version.txt` → `0.7.6`, `idf.py build`, pubblicazione (`docker compose cp ... && secondbrain firmware publish /tmp/fw.bin --version 0.7.6`), una pressione di PWR: il device si aggiorna. `version.txt` → `0.1.0`.

- [ ] **Step 2: Scenario 1 — ritentativo automatico**

`cd backend && docker compose stop app`. Registrazione di qualche secondo → display `server ko`, `coda: 1`. Annotare l'ora. Dopo 5 min `docker compose start app`. Atteso: tra 15 e 16 min dalla registrazione il backend logga `POST /captures` → `201` (`docker compose logs app -t | grep captures`), lo schermo passa a `1 inviate` (un solo refresh), nessun'altra richiesta dopo.

- [ ] **Step 3: Scenario 2 — coda vuota, nessun wake**

Per un'ora dopo lo scenario 1: nessuna richiesta del device nel log del backend. Device in DEV MODE: `/status` → `last_cycle.mode: "TIMER"`, `retry_attempt: 0`, `next_wake_s: 0`, `seq` pari a quello del wake da timer.

- [ ] **Step 4: Scenario 3 — backoff**

`docker compose stop app`, registrazione → `server ko`. Attendere 50 min senza toccare il device (wake a +15 e +45 min, entrambi falliti, display immutato). Device in DEV: `/status` → `mode: "TIMER"`, `retry_attempt: 2`, `next_wake_s: 3600`. Poi `docker compose start app`, PWR breve → `1 inviate`, `/status` dopo il ciclo → `retry_attempt: 0`.

- [ ] **Step 5: Chiusura**

Sul backend resta pubblicata `0.7.6` (o la versione finale). Se emergono bug: fix in un commit `firmware:` con l'esito osservato.

---

### Task 5: Documentazione

**Files:**
- Modify: `README.md` (sezione firmware, voce "Coda" e "Wi-Fi"), `docs/specs/2026-10-07-firmware-timer-sync-design.md` (Stato), `CLAUDE.md` (Stato, Idee future, Storico)

- [ ] **Step 1: README**

Nella lista delle funzionalità del firmware aggiungere la voce: "**Timer di sync**: con file in coda il device si risveglia da solo dopo 15 min (poi 30, 60, 120, 240 min) per riprovare l'upload; a coda vuota dorme finché non si preme PWR. Dopo un wake da timer lo schermo si aggiorna solo se qualcosa è cambiato."

- [ ] **Step 2: Spec e CLAUDE.md**

Stato della spec con data, esiti dei tre scenari e deviazioni. `CLAUDE.md`: in "Stato" la versione del device e una riga sul timer; in "Idee future" togliere "Timer periodico di sync"; nello "Storico" una voce `2026-10-07 — Timer di sync` con i fatti emersi.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/specs/2026-10-07-firmware-timer-sync-design.md CLAUDE.md
git commit -m "docs: timer di sync nel README, stato della spec e CLAUDE.md"
```

---

## Self-Review

- **Copertura**: §3.1-3.2 → Task 1 e 3; §3.3 → Task 1 e 3; §3.4 → Task 2; §4 → Task 1; §5 → Task 2 e 3; §6 → Task 3; §7 → Task 3; §8 → Task 1 (host) e 4 (hardware); §9-10 fuori scopo/rischi.
- **Placeholder**: nessuno; lo Stato della spec si compila con i numeri del Task 4.
- **Nomi**: `sync_retry_delay_ms`, `sync_retry_should_arm`, `status_should_refresh`, `status_outcome_t`, `retry_state_*`, `BOOT_TIMER_SYNC`, `power_deep_sleep(uint32_t)`, `retry_attempt`, `next_wake_s` coerenti tra i task.
- **Review Focus**: 1 → Task 2 (`deadline_cb`); 2 → test host Task 1; 3 → Task 1 (`status_should_refresh`) e Task 3 (`bump` con coda non vuota); 4 → Task 3 e scenario 1; 5 → valori iniziali in `retry_state.c`.
