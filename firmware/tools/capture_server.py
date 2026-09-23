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
import argparse, json, http.server, re, socketserver, struct
from pathlib import Path

INBOX = Path("captures_inbox")
# L'id diventa un nome file: solo caratteri sicuri, niente separatori di percorso.
ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")


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
        if not ID_RE.fullmatch(cid):
            return self._json(400, {"id": cid, "status": "invalid", "reason": "X-Capture-Id non valido"})
        if len(body) < 44 or body[0:4] != b"RIFF" or body[8:12] != b"WAVE":
            return self._json(400, {"id": cid, "status": "invalid", "reason": "header WAV mancante"})
        data_size = struct.unpack("<I", body[40:44])[0]
        if data_size != len(body) - 44:
            print(f"  ATTENZIONE: data_size header={data_size} vs reale={len(body)-44}", flush=True)
        INBOX.mkdir(exist_ok=True)
        dst = (INBOX / f"{cid}.wav").resolve()
        if INBOX.resolve() not in dst.parents:
            return self._json(400, {"id": cid, "status": "invalid", "reason": "percorso non ammesso"})
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
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", a.port), Handler) as srv:
        print(f"capture_server su :{a.port} -> {INBOX.resolve()} (fail_with={a.fail_with})", flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    main()
