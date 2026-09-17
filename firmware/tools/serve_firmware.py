#!/usr/bin/env python3
"""Server statico per testare l'OTA pull. Serve manifest.json e i .bin.

Uso:
    cd firmware && python3 tools/serve_firmware.py 0.2.0
    cd firmware && python3 tools/serve_firmware.py 0.2.0 192.168.1.28
    cd firmware && python3 tools/serve_firmware.py 0.2.0 192.168.1.28 8000

Copia build/secondbrain_fw.bin in ota_serve/firmware/, calcola lo sha256,
scrive manifest.json {version, url, sha256} puntando all'IP LAN del Mac e
serve la directory su :8000 (o la porta indicata).

L'IP viene rilevato automaticamente tramite una socket UDP "connessa" verso
un indirizzo pubblico (non invia nulla, serve solo a far scegliere al kernel
l'interfaccia/route giusta): su macOS, `socket.gethostbyname(gethostname())`
spesso ritorna 127.0.0.1, quindi non lo usiamo. Se il rilevamento fallisce o
va storto, passa esplicitamente l'IP LAN come secondo argomento (per questo
progetto: 192.168.1.28, lo stesso che deve essere in main/secrets.h come
OTA_MANIFEST_URL).
"""
import hashlib
import http.server
import json
import socket
import socketserver
import sys
import shutil
from pathlib import Path


def detect_lan_ip() -> str:
    """Migliore sforzo per trovare l'IP LAN del Mac (non 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        pass
    # Fallback: prova la risoluzione classica, ma scarta 127.0.0.1.
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "0.2.0"
    ip = sys.argv[2] if len(sys.argv) > 2 else detect_lan_ip()
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 8000

    if ip.startswith("127."):
        print(f"ATTENZIONE: IP rilevato {ip} e' localhost, il device sulla LAN "
              f"non potra' raggiungerlo. Passa l'IP LAN esplicito come secondo "
              f"argomento, es: python3 tools/serve_firmware.py {version} 192.168.1.28",
              file=sys.stderr)

    root = Path("ota_serve")
    root.mkdir(exist_ok=True)
    (root / "firmware").mkdir(exist_ok=True)

    src = Path("build/secondbrain_fw.bin")
    if not src.exists():
        sys.exit("build/secondbrain_fw.bin non trovato: esegui prima `idf.py build`")

    dst = root / "firmware" / f"secondbrain-{version}.bin"
    shutil.copy(src, dst)
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()

    manifest = {
        "version": version,
        "url": f"http://{ip}:{port}/firmware/secondbrain-{version}.bin",
        "sha256": sha,
    }
    (root / "firmware" / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"manifest: http://{ip}:{port}/firmware/manifest.json")
    print(json.dumps(manifest, indent=2))
    print(f"(verifica che main/secrets.h abbia OTA_MANIFEST_URL=\"http://{ip}:{port}/firmware/manifest.json\")")

    import os
    os.chdir(root)
    with socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler) as h:
        print(f"serving on :{port} (Ctrl-C per uscire)")
        h.serve_forever()


if __name__ == "__main__":
    main()
