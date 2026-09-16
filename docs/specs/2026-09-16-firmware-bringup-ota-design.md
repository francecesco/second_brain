# Firmware Fase 0 — Bring-up + OTA — Design / Spec

**Data:** 2026-09-16
**Stato:** in revisione (design approvato in chat, spec da rivedere prima del piano).
**Dipende da:** `docs/specs/2026-09-14-second-brain-design.md` (spec di progetto).

---

## 1. Obiettivo

Portare in vita ("bring-up") la scheda **Waveshare ESP32-S3-ePaper-1.54** appena
arrivata e rendere affidabile l'**aggiornamento firmware Over-The-Air (OTA)**, così
il device può stare **solo alimentato** (senza cavo dati verso il Mac). Questa è la
**Fase 0** del progetto Second Brain: conferma l'hardware reale e costruisce le
fondamenta su cui poggeranno le fasi applicative (cattura → vault, display, ecc.).

Non è ancora la logica applicativa completa: qui si validano periferiche e OTA.

## 2. Hardware verificato (2026-09-16)

Scheda collegata via USB-C con microSD inserita. Letta con `esptool 5.3.0`:

| Campo | Valore |
|---|---|
| Chip | **ESP32-S3-PICO-1 (LGA56) rev v0.2** |
| Flash | 8 MB embedded (GD, manuf `c8` dev `4017`) |
| PSRAM | 8 MB |
| Radio | Wi-Fi 2.4 GHz + BT 5 LE |
| USB | USB-Serial/JTAG nativa |
| MAC | `70:04:1d:d8:26:3c` |
| Porta seriale | `/dev/cu.usbmodem1101` |

Periferiche attese (dalla spec di progetto, da confermare in bring-up con la pin-map
Waveshare): e-Paper 1,54" 200×200 B/N, codec **ES8311** (I2S) + mic + speaker,
**SHTC3** (temp/umid), RTC **PCF85063**, slot microSD (FAT32), lettura tensione LiPo.

## 3. Decisioni (approvate in chat)

- **SDK:** **ESP-IDF v5.x** (target `esp32s3`). Da installare sul Mac (assente).
- **OTA:** **entrambi** i modelli.
  - **Pull (produzione):** il device, nella finestra di sync sul Wi-Fi di casa,
    interroga un **manifest JSON**; se c'è una versione più nuova, scarica il `.bin`,
    ne verifica lo **sha256**, lo scrive nella partizione inattiva e riavvia.
  - **Push (sviluppo):** tenendo premuto un tasto al power-on il device entra in
    *dev mode*, resta sveglio ed espone `POST /ota` per ricevere il `.bin` via Wi-Fi
    (nessun cavo dati).
- **Sorgente pull per ora:** **server statico minimo sul Mac** (`tools/serve_firmware.py`)
  che serve manifest + `.bin`. Lo **stesso contratto** diventerà l'endpoint
  `/firmware` del backend `secondbrain` in Fase 1.
- **Rollback:** doppia partizione app + `otadata`, con **rollback automatico** del
  bootloader se la nuova app non conferma la validità dopo il boot.
- **Segreti:** Wi-Fi SSID/password + URL manifest in un **`secrets.h` gitignored**
  (da `secrets.h.example`). Provisioning evoluto (NVS/BLE) fuori scope ora (YAGNI).
- **Repo:** tutto il firmware vive in `second_brain/firmware/`. **Nessuna
  attribuzione all'assistente** nei commit; identità git `francecesco`.

## 4. Struttura del firmware

Moduli a responsabilità singola; `app_main.c` è l'unico che li orchestra. I pin
vivono tutti in `board.h` (i "da confermare" della spec in un solo posto).

```
firmware/
├── CMakeLists.txt
├── sdkconfig.defaults
├── partitions.csv
├── version.txt              # versione firmware, embeddata a build-time
├── main/
│   ├── CMakeLists.txt
│   ├── app_main.c           # macchina a stati + scelta boot mode (normale/dev)
│   ├── board.h              # pin-map (da repo Waveshare)
│   ├── secrets.h.example    # template; secrets.h reale è gitignored
│   ├── display.[ch]         # e-Paper 200×200 1-bit: init, testo, blit BMP
│   ├── audio.[ch]           # ES8311 + I2S → WAV 16 kHz mono
│   ├── storage.[ch]         # SD/FAT32 mount + scrittura/lettura file
│   ├── sensors.[ch]         # SHTC3, RTC PCF85063, ADC tensione batteria
│   ├── power.[ch]           # deep sleep + wake su tasto
│   ├── wifi.[ch]            # connessione Wi-Fi da secrets/NVS
│   └── ota.[ch]             # pull (manifest), push dev (POST /ota), rollback confirm
├── components/              # driver terzi (e-Paper, es8311) se non in-tree
├── host_test/               # unit test host-target (logica pura: manifest, semver)
└── tools/
    └── serve_firmware.py    # server statico + manifest per test OTA pull
```

Ogni modulo espone un'interfaccia piccola e testabile; l'audio dipende dallo storage
(scrive su SD), l'OTA dipende da wifi. Nulla dipende da `app_main`.

## 5. Sequenza di bring-up (incrementale)

Ordinata per dipendenze reali. Ogni tappa ha un **criterio di passaggio** verificato
sull'hardware (via seriale e/o e-Paper) prima di procedere.

| # | Tappa | Criterio di passaggio |
|---|---|---|
| 1 | Toolchain + "hello" | `idf.py build flash monitor` ok su `/dev/cu.usbmodem1101`; log su seriale. |
| 2 | e-Paper | Testo visibile sul display; confermati controller e modalità di refresh (pieno/parziale). |
| 3 | Tasti | Identificati i GPIO di PWR / BOOT / tasto utente; pressione rilevata a log. |
| 4 | SD card | microSD montata FAT32; file scritto e riletto correttamente. |
| 5 | Audio | ES8311 via I2S registra un WAV **16 kHz mono** su SD; file riproducibile sul Mac. |
| 6 | Sensori | Letture plausibili di SHTC3 (temp/umid), RTC (data/ora), ADC tensione batteria. |
| 7 | Deep sleep | Il device dorme e si sveglia su tasto; consumo in sleep misurato. |
| 8 | Wi-Fi | Connessione alla rete di casa con creds da `secrets.h`; IP ottenuto. |
| 9 | OTA | Pull da server statico applica un nuovo `.bin`; push dev via `curl` applica un `.bin`; rollback verificato con firmware "cattivo". |

## 6. Design OTA (dettaglio)

### 6.1 Partizioni (`partitions.csv`, flash 8 MB)

Tabella con doppia slot app per abilitare il rollback:

| Nome | Tipo | SubType | Dimensione (indicativa) |
|---|---|---|---|
| `nvs` | data | nvs | 24 KB |
| `otadata` | data | ota | 8 KB |
| `phy_init` | data | phy | 4 KB |
| `ota_0` | app | ota_0 | ~3 MB |
| `ota_1` | app | ota_1 | ~3 MB |

Storage di massa (WAV, cache digest) resta su **microSD**, non in flash → non serve
una partizione FAT interna grande. Dimensioni esatte fissate nel piano.

### 6.2 Contratto manifest (riusato dal backend in Fase 1)

```json
{
  "version": "0.2.0",
  "url": "http://<host>:8000/firmware/secondbrain-0.2.0.bin",
  "sha256": "<hex a 64 caratteri>"
}
```

La versione del firmware in esecuzione è embeddata a build-time (da `version.txt`).
Confronto **semver**: si aggiorna solo se `manifest.version > versione corrente`.

### 6.3 Flusso pull (produzione)

1. Nella finestra di sync, dopo la connessione Wi-Fi, GET del manifest.
2. Se la versione è più nuova: download del `.bin` con verifica **sha256**.
3. Scrittura nella partizione inattiva (`esp_ota_*`), set del boot, reboot.
4. Per ora l'host del manifest è il server statico sul Mac; in Fase 1 diventa
   l'endpoint `/firmware` del backend, **senza modifiche al device** (stesso contratto).

### 6.4 Flusso push dev (sviluppo, senza cavo)

1. Tenendo premuto **BOOT / tasto utente al power-on**, il device entra in *dev mode*:
   resta sveglio, si collega al Wi-Fi ed espone un mini HTTP server `POST /ota`.
2. Dal Mac: `curl --data-binary @build/secondbrain.bin http://<device-ip>/ota`.
3. Il body viene scritto in streaming nella partizione inattiva via `esp_ota_*`, poi reboot.
4. Puro ESP-IDF, nessun protocollo esterno.

### 6.5 Rollback confirm

Dopo un OTA, al boot successivo l'app esegue un **self-check minimo** (boot completo +
Wi-Fi ok); solo allora chiama `esp_ota_mark_app_valid_cancel_rollback()`. Se l'app
va in crash o non conferma entro il boot, il bootloader **ripristina automaticamente**
la slot precedente. È ciò che rende sicuro l'update "solo alimentato".

## 7. Config & segreti

- `secrets.h` (gitignored, da `secrets.h.example`): `WIFI_SSID`, `WIFI_PASSWORD`,
  `OTA_MANIFEST_URL`.
- `.gitignore` del firmware esclude `secrets.h`, `build/`, `sdkconfig` locale se necessario.
- Provisioning evoluto (NVS runtime / BLE) rimandato a fase successiva.

## 8. Testing

- **Host-target unit test** (`host_test/`, Unity/`idf.py --preview set-target linux`):
  logica **pura** senza hardware — parsing del manifest JSON e confronto versioni semver
  (inclusi casi limite: versione uguale, più vecchia, malformata).
- **Hardware-in-the-loop:** ogni tappa della sezione 5 ha un criterio esplicito
  verificato manualmente via seriale/e-Paper.
- **OTA end-to-end:** pull contro `serve_firmware.py`; push via `curl`; rollback
  provato flashando di proposito un firmware che fallisce il self-check.

## 9. Non-goals (YAGNI, per ora)

- Logica applicativa di cattura completa e digest server-side (fasi successive).
- Endpoint `/firmware` nel backend reale (Fase 1; ora solo server statico).
- Provisioning Wi-Fi evoluto (NVS runtime/BLE).
- Cifratura del firmware / secure boot (valutabile più avanti).

## 10. Rischi e cose da confermare in bring-up

- **Pin-map e driver reali** (e-Paper, ES8311, SD, ADC batteria): da ricavare dal repo
  Waveshare `ESP32-S3-ePaper-1.54G` e dai datasheet; centralizzati in `board.h`.
- **Refresh parziale** dell'e-Paper: da confermare quale controller e se supportato.
- **Consumo in deep sleep**: da misurare; influenza l'autonomia a batteria.
- **Coesistenza SD + I2S + Wi-Fi** sui bus/GPIO: verificare che non ci siano conflitti.
```
