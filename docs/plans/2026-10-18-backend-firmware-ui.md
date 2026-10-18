# Piano — Pubblicazione del firmware dalla pagina Dispositivi

Spec: `docs/specs/2026-10-18-backend-firmware-ui-design.md`. TDD: prima il test rosso.
Branch `backend-firmware-ui`. Le checkbox non si spuntano: lo stato è nei commit.

- [ ] **Task 1 — `ota.read_app_desc`**: funzione pura che da `bytes` ritorna `(version,
  project)` o solleva `OtaError` (magic, progetto, versione). Test con un binario finto
  costruito a mano e con i casi d'errore.
- [ ] **Task 2 — `ota.publish_bytes`**: pubblica da contenuto in memoria con la versione
  dell'intestazione (scrive un file temporaneo e usa `publish`). Test: release corrente,
  duplicato rifiutato.
- [ ] **Task 3 — Route e card**: `POST /devices/firmware/{type}` e `.../rollback`, template
  `devices.html` con la card, notice su `pubblicata`, `corrente`, `errore`. Test con il
  client loggato: upload buono (303, release corrente, file su disco), file non firmware
  (303 con `errore`), rollback, CSRF obbligatorio.
- [ ] **Task 4 — CLI**: `--version` opzionale in `firmware publish`. Test.
- [ ] **Task 5 — Script `firmware/tools/release.sh`**, `CLAUDE.md` (procedura standard,
  cavo solo per il recupero), `README.md` se cita il publish da CLI.
- [ ] **Task 6 — Prova in locale** sul Mac (`docker compose up -d --build`, screenshot
  chiaro/scuro a 1280 e 390 px), poi deploy sulla ZimaBoard su decisione dell'autore.
