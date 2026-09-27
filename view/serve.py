"""Local server: viewer, map data, thumbnails, text peeks, search by meaning, reveal/open."""

import json
import mimetypes
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

from . import embed as E

WEB = Path(__file__).parent / "web"


def serve(root: Path, files, models, vectors, port: int, open_browser: bool):
    out, thumbs = root / ".view" / "map", root / ".view" / "thumbs"
    loaded, lock = {}, threading.Lock()
    kinds = np.array([f.kind for f in files])

    def encoder(model):
        with lock:
            if model not in loaded:
                loaded[model] = E.load(model)
            return loaded[model]

    threading.Thread(target=encoder, args=(models[0],), daemon=True).start()  # warm up search

    def search(q: str, model: str, k=200):
        v = np.asarray(encoder(model).texts([q]), np.float32)[0]
        scores = vectors[model] @ (v / np.linalg.norm(v))
        # text-text similarity always beats text-image (the modality gap), so rank within each kind
        for kind in set(kinds):
            s = scores[kinds == kind]
            scores[kinds == kind] = (s - s.mean()) / (s.std() + 1e-6)
        top = np.argsort(-scores)[:k]
        return [[int(i), round(float(scores[i]), 4)] for i in top]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype="application/json", status=200):
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def file(self, s):
            return files[int(s)] if s.isdigit() and int(s) < len(files) else None

        def do_GET(self):
            path = self.path.split("?")[0]
            kind, _, arg = path.strip("/").partition("/")
            f = self.file(arg)
            if kind == "data" and (out / arg).is_file() and "/" not in arg:
                return self.send((out / arg).read_bytes(), mimetypes.guess_type(arg)[0] or "application/octet-stream")
            if kind == "thumb" and f:
                t = thumbs / E.thumb_name(f)
                if not t.exists():
                    im = E.rgb(Image.open(root / f.rel))
                    im.thumbnail((256, 256))
                    im.save(t, "JPEG", quality=82)
                return self.send(t.read_bytes(), "image/jpeg")
            if kind == "peek" and f:
                return self.send(E.read_text(root / f.rel, 2000).encode(), "text/plain; charset=utf-8")
            static = (WEB / (path.lstrip("/") or "index.html")).resolve()
            if static.is_relative_to(WEB) and static.is_file():
                return self.send(static.read_bytes(), mimetypes.guess_type(static.name)[0] or "text/plain")
            self.send(b"not found", "text/plain", 404)

        def do_POST(self):
            if self.headers.get("X-View") != "1":  # forces a CORS preflight: other sites can't call this
                return self.send(b"forbidden", "text/plain", 403)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            kind, _, arg = self.path.strip("/").removeprefix("api/").partition("/")
            if kind == "search" and body.get("model") in vectors:
                return self.send(search(body["q"], body["model"]))
            f = self.file(arg)
            if kind in ("reveal", "open") and f:
                subprocess.run(["open", "-R", str(root / f.rel)] if kind == "reveal" else ["open", str(root / f.rel)])
                return self.send({"ok": True})
            self.send(b"not found", "text/plain", 404)

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"\n  viewing  {url}   (ctrl-c to stop)\n")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
