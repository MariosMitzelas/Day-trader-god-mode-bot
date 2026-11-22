"""Webhook receiver."""
import argparse, hashlib, hmac, json, logging, os, sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger("webhook")
SCHEMA = """CREATE TABLE IF NOT EXISTS webhook_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL,
 source TEXT NOT NULL, payload TEXT NOT NULL,
 signature_ok INTEGER NOT NULL DEFAULT 0);"""

def _now(): return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def _verify(body, header, secret):
    if not secret: return True
    if not header: return False
    exp = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, header.strip())

def _store(db_path, source, payload, ok):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO webhook_events (received_at, source, payload, signature_ok) "
                   "VALUES (?, ?, ?, ?)",
                   (_now(), source, json.dumps(payload, ensure_ascii=False), int(ok)))

class Handler(BaseHTTPRequestHandler):
    db_path = "state/webhooks.sqlite3"
    secret = None

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz": return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/scanner", "/alertmanager"):
            return self._json(404, {"error": "not found"})
        source = self.path.lstrip("/")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(n)
            if not _verify(body, self.headers.get("X-Signature"), self.secret):
                return self._json(401, {"error": "bad signature"})
            payload = json.loads(body) if body else {}
        except Exception as e:
            return self._json(400, {"error": str(e)})
        try:
            _store(self.db_path, source, payload, True)
        except Exception as e:
            return self._json(500, {"error": str(e)})
        return self._json(202, {"ok": True, "source": source})

    def log_message(self, *a): pass

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=8090)
    p.add_argument("--db", default="state/webhooks.sqlite3")
    a = p.parse_args()
    Handler.db_path = a.db
    Handler.secret = os.environ.get("WEBHOOK_SECRET")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()

if __name__ == "__main__":
    main()
