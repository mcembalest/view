"""Tiny local server: the viewer, the map data, thumbnails, and reveal-in-Finder."""

import json
import mimetypes
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

WEB = Path(__file__).parent / "web"


def serve(root: Path, map_dir: Path, port: int, open_browser: bool):
    meta = json.loads((map_dir / "meta.json").read_text())
    rels = [it["path"] for it in meta["items"]]
    thumbs = root / ".view" / "thumbs"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body: bytes, ctype: str, status=200, cache=False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def item(self, s: str) -> Path | None:
            i = int(s) if s.isdigit() else -1
            return root / rels[i] if 0 <= i < len(rels) else None

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                path = "/index.html"
            if path.startswith("/data/"):
                f = map_dir / path[6:]
                if f.parent == map_dir and f.exists():
                    return self.send(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            elif path.startswith("/thumb/"):
                p = self.item(path[7:])
                if p:
                    t = thumbs / meta["items"][int(path[7:])]["thumb"]
                    if not t.exists():
                        im = Image.open(p).convert("RGB")
                        im.thumbnail((256, 256))
                        im.save(t, "JPEG", quality=82)
                    return self.send(t.read_bytes(), "image/jpeg", cache=True)
            elif path.startswith("/file/"):
                p = self.item(path[6:])
                if p and p.exists():
                    return self.send(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "text/plain; charset=utf-8")
            else:
                f = (WEB / path.lstrip("/")).resolve()
                if f.is_relative_to(WEB) and f.is_file():
                    return self.send(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send(b"not found", "text/plain", 404)

        def do_POST(self):
            # custom header forces a CORS preflight, so other websites can't trigger this
            if self.headers.get("X-View") != "1":
                return self.send(b"forbidden", "text/plain", 403)
            action, _, i = self.path.strip("/").removeprefix("api/").partition("/")
            p = self.item(i)
            if p and action in ("reveal", "open"):
                subprocess.run(["open", "-R", str(p)] if action == "reveal" else ["open", str(p)])
                return self.send(b"ok", "text/plain")
            self.send(b"not found", "text/plain", 404)

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"\n  viewing  {url}   (ctrl-c to stop)\n")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
        sys.exit(0)
