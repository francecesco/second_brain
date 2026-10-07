# Firmware — HTTPS e token verso il backend — Design / Spec

**Data:** 2026-10-07
**Stato:** implementato e **verificato sull'hardware il 2026-10-07** via Wi-Fi (branch
`firmware-https-token`, piano `docs/plans/2026-10-07-firmware-https-token.md`, 31 test host).
Verificati con un quick tunnel di Cloudflare (`*.trycloudflare.com`): token sbagliato →
`401` dal tunnel, file conservato in `queue/`, display `token ko`, `/status` con
`last_http: 401`; token giusto → upload `201` e OTA pull `0.7.0 → 0.7.1` in HTTPS (manifest
con `url` `https://` composto dal backend dallo schema inoltrato); in LAN in HTTP senza
token upload `201` come prima; OTA pull `0.7.2 → 0.7.3` in LAN con token. Heap libero
minimo nel ciclo con upload HTTPS: 123 924 byte (nessun `NO_MEM`); il valore dopo il
download HTTPS non è stato letto (`diag` si azzera al riavvio post-OTA). Dalla revisione finale: `last_http` torna a `0` anche su errore di rete dopo un upload
riuscito nello stesso ciclo; `free_heap_min` usa il low-watermark dal boot (il valore sopra
era un campione prima e dopo la richiesta, quindi una stima per eccesso); un token
rifiutato sul manifest a coda vuota mostra `token ko` (verificato il 2026-10-07 ruotando il
token sul backend: `token ko`, poi `sync ok` dopo il ripristino; device sulla 0.7.4). Deviazioni:
`http_status_is_auth_error` sta in `sync_policy.c` (pura, test host) e non in
`http_client.c`; il quick tunnel ha bisogno di circa un minuto dopo l'avvio prima di
rispondere. Raccoglie i punti rimandati in §11 della spec `2026-09-28-backend-archivio-design.md`.

## 1. Obiettivo

Il device deve poter caricare le registrazioni e fare l'OTA pull attraverso il tunnel
Cloudflare della ZimaBoard: connessione HTTPS verificata con il bundle di certificati di
ESP-IDF e autenticazione con il token per dispositivo che il backend già conosce
(`Authorization: Bearer <token>`, `secondbrain device token <mac>`).

Prima di attivare il token, il firmware deve smettere di trattare `401`, `403` e `429` come
rifiuti definitivi: oggi ogni `4xx` sposta il file in `queue/rejected/`, e con un token
sbagliato o scaduto l'intera coda finirebbe lì al primo ciclo.

## 2. Contesto e vincoli

- Contratto del backend (spec Fase 1b §6 e §9): `POST /captures` risponde `401` con token
  assente o non valido, `403` se `X-Device-Id` non corrisponde al token, `429` non è
  previsto oggi ma può arrivare da Cloudflare (rate limit, Bot Fight Mode). `GET
  /firmware/manifest.json` e il binario richiedono lo stesso token; senza, dal tunnel
  rispondono `401`.
- `ALLOW_UNAUTHENTICATED_LAN=true` sul backend accetta richieste senza token dalla LAN:
  il firmware resta compatibile con questa modalità quando `DEVICE_TOKEN` è vuoto.
- Il client HTTP di ESP-IDF (`esp_http_client`) gestisce da solo `http://` e `https://`
  dallo schema dell'URL; per `https://` serve `crt_bundle_attach`. Nel `sdkconfig` sono già
  attivi `CONFIG_ESP_HTTP_CLIENT_ENABLE_HTTPS` e `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE`
  (bundle completo). `CONFIG_MBEDTLS_HAVE_TIME_DATE` non è impostato: le date di validità
  dei certificati non vengono controllate, quindi un RTC azzerato (senza batteria succede a
  ogni taglio di alimentazione) non impedisce la connessione; in ogni caso `timesync_run`
  precede il sync.
- La scheda non ha PSRAM: una sessione TLS costa circa 40–50 KB di heap (buffer di
  ricezione da 16 KB, handshake, chiavi). Durante il sync sono già allocati il buffer di
  upload da 16 KB e l'elenco della coda; l'audio è chiuso. Va misurato sull'hardware.
- Spazio flash: binario attuale 1,16 MB su partizioni app da 3 MB; il bundle completo pesa
  meno di 100 KB.
- Limite del piano gratuito di Cloudflare: 100 MB per richiesta (una cattura da 10 min è
  circa 19 MB, sotto il limite).

## 3. Decisioni (approvate in chat)

1. **Un solo URL del server**, `SERVER_BASE_URL` in `secrets.h`. Il campo `server` per
   rete di `WIFI_NETWORKS` sparisce: a casa il device passa dal tunnel anche se la
   ZimaBoard è sulla stessa LAN. Configurazione più semplice, un solo percorso da
   verificare.
2. **Token in `secrets.h`** (`DEVICE_TOKEN`), a compile time come le reti Wi-Fi. Vuoto =
   nessun header `Authorization` (modalità LAN senza token).
3. **`401` e `403` fermano il sync senza toccare la coda** e vengono segnalati come errore
   di autorizzazione; **`429`** ferma il sync come un `5xx`. Gli altri `4xx` restano
   rifiuti definitivi (`rejected/`).
4. **Un solo punto di configurazione del client HTTP** (`http_client.c`) usato da upload,
   manifest e download del firmware: bundle di certificati e header del token non vanno
   duplicati in tre posti.
5. **Verifica con un quick tunnel di Cloudflare** (`cloudflared tunnel --url
   http://localhost:8000`, senza account, hostname `*.trycloudflare.com` con certificato
   pubblico): TLS vero e, arrivando da un IP pubblico, token obbligatorio lato backend. Il
   tunnel definitivo della ZimaBoard arriva col deploy (Task 14 della Fase 1b).

## 4. Configurazione (`secrets.h`)

```c
#define WIFI_NETWORKS { \
    { "casa-ssid",    "password-casa",    "54:78:f0:bd:65:eb" }, \
    { "ufficio-ssid", "password-ufficio", NULL }, \
}
// Base URL del backend, uguale su tutte le reti: https://ingest.<dominio> dietro il
// tunnel, oppure http://<ip>:8000 per un backend in LAN. Da qui derivano
// POST /captures e GET /firmware/manifest.json.
#define SERVER_BASE_URL "https://ingest.esempio.it"
// Token del dispositivo, stampato da `secondbrain device add` o `device token`.
// Vuoto: nessun header Authorization (solo con ALLOW_UNAUTHENTICATED_LAN sul backend).
#define DEVICE_TOKEN "..."
```

- `wifi_network_t` perde il campo `server`; `wifi_server_base_url()` sparisce da `wifi.h`
  e dai chiamanti (`sync.c`, `app_main.c`). Il log di connessione stampa `SERVER_BASE_URL`.
- Il test host `test_wifi_select.c` perde il quarto campo delle voci.
- `secrets.h.example` e `README.md` (radice) aggiornati (sezione configurazione, "Ogni
  rete ha il proprio server" sparisce).

## 5. Client HTTP (`http_client.c`, `http_client.h`)

```c
// Configurazione comune: URL, metodo, timeout di inattivita', bundle di certificati per
// gli URL https:// (ignorato per http://).
esp_http_client_config_t http_client_config(const char *url, esp_http_client_method_t method,
                                            int timeout_ms);
// Aggiunge "Authorization: Bearer <DEVICE_TOKEN>" se il token non e' vuoto.
void http_client_set_auth(esp_http_client_handle_t client);
// Vero se lo status e' un rifiuto di autorizzazione (401, 403).
bool http_status_is_auth_error(int status);
```

- `sync.c`: `upload_one` usa `http_client_config` e `http_client_set_auth`; l'URL è
  `SERVER_BASE_URL "/captures"`.
- `ota.c`: `http_get_to_buffer` (manifest) e `ota_download_and_apply` (binario) usano la
  stessa configurazione e lo stesso header. Il manifest viene letto da
  `SERVER_BASE_URL "/firmware/manifest.json"`; l'`url` del binario nel manifest è
  assoluto e lo compone il backend dallo schema e host della richiesta (`https://` dietro
  il tunnel).
- Log dell'heap libero (`esp_get_free_heap_size`) prima e dopo l'upload e dopo il download,
  per leggere il costo del TLS dal seriale o da `/status`.

## 6. Politica degli esiti (`sync_policy.c`)

| Status | Azione | Effetto |
|---|---|---|
| `200`, `201`, `409` | `SYNC_ACTION_DELETE` | file cancellato, `sent++` |
| `401`, `403` | `SYNC_ACTION_STOP_AUTH` | file conservato, sync interrotto, `auth_error = true` |
| `429`, `5xx`, redirect, `<= 0` | `SYNC_ACTION_STOP` | file conservato, sync interrotto, `server_error = true` |
| altri `4xx` | `SYNC_ACTION_REJECT` | file in `rejected/`, `rejected++`, si prosegue |

- `sync_result_t` acquista `bool auth_error`; `sync_run` ritorna `ESP_FAIL` in entrambi i
  casi di stop, come oggi.
- OTA: un manifest (o binario) con `401`/`403` viene loggato come "token rifiutato",
  `ota_pull` ritorna `ESP_ERR_NOT_ALLOWED` e il ciclo segnala `auth_error` anche a coda
  vuota: altrimenti dopo una rotazione del token il display direbbe `sync ok`.
- Funzione pura, test host in `test_policies.c` aggiornato prima dell'implementazione.

## 7. Display e diagnostica

- Riga 4 del display di stato: `token ko` quando `auth_error` (precede `server ko`).
- `diag_t` acquista `int32_t last_http` (status dell'ultimo upload, `0` se nessuno o
  errore di rete) e `uint32_t free_heap_min` (low-watermark dell'heap dal boot,
  `esp_get_minimum_free_heap_size`, letto prima e dopo le richieste: un ciclo è un boot);
  `GET /status` li espone in `last_cycle` come `last_http` e `free_heap_min`. Serve a
  diagnosticare senza seriale.

## 8. Backend e documentazione

- Nessuna modifica al codice del backend.
- `backend/README.md`: il paragrafo "cosa fa il device con ciascun esito" descrive la nuova
  politica (`401`/`403` → tiene il file e segnala; `429` → come `5xx`); la nota su
  `ALLOW_UNAUTHENTICATED_LAN` diventa "può tornare `false` quando tutti i dispositivi
  mandano il token".
- `README.md` (radice, sezione firmware): configurazione (`SERVER_BASE_URL`, `DEVICE_TOKEN`),
  comportamento della coda, HTTPS.
- `CLAUDE.md`: stato e "Prossima sessione".

## 9. Verifica

**Host** (`cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf`):
`sync_decide` con `401`, `403` → `STOP_AUTH`, `429` → `STOP`, gli altri casi invariati;
`wifi_select` con le voci a tre campi. I test devono restare tutti verdi.

**Hardware**, via Wi-Fi dall'ufficio (device con sola alimentazione USB: push della build
di prova con `POST /ota` in DEV MODE, versione locale `0.7.0` non committata). Backend sul
Mac, esposto con un quick tunnel di Cloudflare per gli scenari in HTTPS:

1. **Token sbagliato, HTTPS dal tunnel**: `DEVICE_TOKEN` errato, una registrazione → upload
   `401` dal backend, file ancora in `queue/`, display `token ko`, `/status` con
   `last_http: 401`. Il backend logga l'IP pubblico dell'ufficio in `Cf-Connecting-Ip`.
2. **Token giusto, HTTPS dal tunnel**: la stessa registrazione viene caricata (`201`) e
   compare nel finder; `secondbrain device list` mostra `token=sì` e l'ultimo contatto.
3. **OTA pull con token dal tunnel**: `secondbrain firmware publish` di una `0.7.1`; al
   ciclo successivo il device la scarica in HTTPS, verifica lo sha256 e riavvia; conferma
   dopo l'init del display.
4. **LAN in HTTP, senza token**: `SERVER_BASE_URL` sul Mac, `DEVICE_TOKEN` vuoto,
   `ALLOW_UNAUTHENTICATED_LAN=true`: upload `201` come prima della modifica.
5. **Heap**: dal log, heap libero prima e dopo l'upload HTTPS e dopo il download; nessun
   `ESP_ERR_NO_MEM` né reset.
6. **Rifiuti definitivi invariati**: un WAV corrotto in coda → `422` → `rejected/`.

Alla fine si aggiorna lo "Stato" di questa spec con data e deviazioni; `secrets.h` del
device punta al Mac finché la ZimaBoard non è in produzione.

## 10. Non-goals

- Service token di Cloudflare Access (`CF-Access-Client-Id`/`Secret`): seconda barriera
  opzionale, rimandata.
- Provisioning di Wi-Fi, URL e token senza ricompilare (NVS/BLE).
- Timer periodico di sync, refresh parziale, digest sul display.
- Rotazione del token dal device; un token nuovo si mette in `secrets.h` e si riflasha.

## 11. Rischi

- **Heap**: senza PSRAM il TLS potrebbe non trovare 40–50 KB contigui dopo un ciclo di
  cattura. Mitigazioni, in ordine: chiudere il canale audio e liberare i buffer prima del
  Wi-Fi (già così), `CONFIG_MBEDTLS_DYNAMIC_BUFFER`, ridurre
  `CONFIG_MBEDTLS_SSL_IN_CONTENT_LEN` solo se il backend non manda risposte grandi.
- **Prima connessione dopo l'associazione**: oggi c'è un retry sull'`open`; con TLS
  l'handshake aggiunge ~1–2 s per connessione, da tenere presente nel budget di sync
  (3 min) e nella scadenza di ciclo (5 min).
- **Quick tunnel**: l'hostname `*.trycloudflare.com` cambia a ogni avvio e va ricompilato
  in `secrets.h`; serve solo per la verifica, non per l'uso.
- **Cloudflare davanti ai dispositivi**: Bot Fight Mode o WAF possono rispondere `403`
  o `429` a un client senza browser; per questo quei codici non devono mai svuotare la
  coda. Sull'hostname dei dispositivi vanno disattivati (Task 14).
