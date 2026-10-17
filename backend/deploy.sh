#!/usr/bin/env bash
# Deploy in produzione (ZimaBoard): backup del database, aggiornamento del codice da GitHub,
# ricostruzione di app e worker. Si lancia dal clone del repo: ~/second_brain/backend/deploy.sh
# Il .env (gitignored) sta accanto a questo file; i dati in $DATA_DIR non si toccano.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "manca .env in $(pwd)" >&2; exit 1; }
DATA_DIR=$(sed -n 's/^DATA_DIR=//p' .env | tail -n 1)
case "$DATA_DIR" in
  /*) ;;
  *) echo "DATA_DIR nel .env deve essere un percorso assoluto (trovato: '${DATA_DIR}')" >&2; exit 1 ;;
esac
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ci sono modifiche locali nel repo: niente deploy finché non sono sistemate" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

# 1. Backup del database (catalogo; l'archivio su disco resta la fonte di verità)
BACKUP_DIR="$DATA_DIR/backup"
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T postgres pg_dump -U secondbrain secondbrain | gzip > "$BACKUP"
[ "$(gzip -dc "$BACKUP" | head -c 1 | wc -c)" -eq 1 ] || { echo "backup vuoto: $BACKUP" >&2; exit 1; }
echo "backup: $BACKUP ($(du -h "$BACKUP" | cut -f1))"

# 2. Codice: solo avanti veloce da GitHub
OLD=$(git rev-parse --short HEAD)
git pull --ff-only
NEW=$(git rev-parse --short HEAD)
if [ "$OLD" = "$NEW" ]; then
  echo "codice già aggiornato ($NEW): ricostruisco comunque"
else
  echo "codice: $OLD -> $NEW"
  git log --oneline "$OLD..$NEW"
fi

# 3. Ricostruzione e riavvio; le migrazioni le applica l'app all'avvio
docker compose up -d --build
docker compose ps
echo
echo "fatto. Per tornare indietro: git checkout $OLD && docker compose up -d --build"
echo "(poi git checkout master prima del deploy successivo)"
echo "(e, se serve, il database da $BACKUP)"
