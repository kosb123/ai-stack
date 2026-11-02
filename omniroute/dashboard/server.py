"""OmniRoute overview dashboard: static files, JSON API and a background collector, all on 127.0.0.1."""
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import api
import collect

HOST, PORT = "127.0.0.1", 20140
COLLECT_EVERY_SEC = 120
HERE = Path(__file__).resolve().parent
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
log = logging.getLogger("omni-dash")


def run_collector(stop, src=collect.DEFAULT_SRC, dst=collect.DEFAULT_DST, every=COLLECT_EVERY_SEC):
    while not stop.is_set():
        try:
            counts = collect.collect(src, dst)
            log.info("collected %s", counts)
        except Exception:
            log.exception("collect failed")
        stop.wait(every)


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                try:
                    self._send(200, (HERE / name).read_bytes(), ctype)
                except OSError:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                code, payload = api.handle(db_path, url.path, params)
            except Exception as e:
                log.exception("api failed: %s", url.path)
                code, payload = 500, {"error": f"{type(e).__name__}: {e}"}
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def serve(db_path=collect.DEFAULT_DST, src=collect.DEFAULT_SRC, host=HOST, port=PORT, collector=True):
    stop = threading.Event()
    collect.open_dst(db_path).close()
    if collector:
        threading.Thread(target=run_collector, args=(stop, src, db_path), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    return httpd, stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    httpd, stop = serve()
    print(f"http://{HOST}:{PORT}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
