#!/bin/sh
# Compila una release del firmware da caricare nella pagina Dispositivi del backend.
#   firmware/tools/release.sh 0.9.2
# Scrive version.txt, compila, copia il binario in ~/Downloads/secondbrain-0.9.2.bin e rimette
# version.txt a 0.1.0 (in git resta sempre 0.1.0: la versione vive nella release pubblicata).
# Serve ESP-IDF esportato (. ~/esp/esp-idf/export.sh).
set -eu
case "${1:-}" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "uso: $0 X.Y.Z" >&2; exit 2 ;;
esac
version="$1"
dir="$(cd "$(dirname "$0")/.." && pwd)"
out="${2:-$HOME/Downloads}/secondbrain-$version.bin"
command -v idf.py >/dev/null || { echo "idf.py non trovato: . ~/esp/esp-idf/export.sh" >&2; exit 1; }
cd "$dir"
restore() { echo 0.1.0 > version.txt; }
trap restore EXIT
echo "$version" > version.txt
idf.py build
mkdir -p "$(dirname "$out")"
cp build/secondbrain_fw.bin "$out"
echo "release $version pronta: $out"
echo "caricala in Dispositivi > Firmware; il device la prende al prossimo risveglio"
