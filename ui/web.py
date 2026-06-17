"""web.py — local web dashboard for bot-suite (stdlib only, no dependencies).

Serves a single-page control panel on 127.0.0.1 and a small JSON API that wires
every button to shared/service.py (the same operations the CLI uses).

Built on Python's standard-library http.server so it needs ZERO extra packages
— it runs on any Python the bot already runs on.

Launch:  ./bot web          (or: python -m ui.web)
Opens:   http://127.0.0.1:8787

Local only by design: binds to 127.0.0.1, nothing is exposed to the network.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import service

INDEX_HTML = Path(__file__).resolve().parent / "static" / "index.html"
_BIZ_ID_RE = re.compile(r"^/api/businesses/(\d+)$")

# Loopback names always trusted; the actual bind host is added in run_server().
_DEFAULT_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _host_only(value: str) -> str:
    """Extract the hostname from a Host/Origin authority, dropping the port.

    Handles 'localhost:8787', '127.0.0.1', and IPv6 '[::1]:8787'.
    """
    value = (value or "").strip()
    if value.startswith("["):  # IPv6 literal: [::1] or [::1]:port
        return value[1:].split("]", 1)[0].lower()
    if ":" in value:
        return value.rsplit(":", 1)[0].lower()
    return value.lower()


def _json_default(o):
    """Make Postgres types JSON-serializable (Decimal, dates, etc.)."""
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    return str(o)


# ──────────────────────────────────────────────
# Validation helpers (boundary checks — no framework)
# ──────────────────────────────────────────────

class BadRequest(Exception):
    """Raised for invalid client input -> 400."""


def _require(cond: bool, msg: str):
    if not cond:
        raise BadRequest(msg)


def _str(d: dict, key: str, *, max_len: int = 500) -> str:
    v = d.get(key, "")
    _require(isinstance(v, str), f"{key} must be a string")
    _require(len(v) <= max_len, f"{key} too long")
    return v.strip()


def _validate_run(body: dict) -> dict:
    sessions = body.get("sessions")
    _require(isinstance(sessions, int), "sessions must be an integer")
    _require(1 <= sessions <= 1000, "sessions must be between 1 and 1000")

    proxy_mode = body.get("proxy_mode", "regular")
    _require(proxy_mode in ("regular", "off", "free"),
             "proxy_mode must be 'regular', 'off' or 'free'")

    min_delay = body.get("min_delay")
    max_delay = body.get("max_delay")
    for name, val in (("min_delay", min_delay), ("max_delay", max_delay)):
        if val is not None:
            _require(isinstance(val, int), f"{name} must be an integer")
            _require(0 <= val <= 3600, f"{name} must be between 0 and 3600 seconds")
    if min_delay is not None and max_delay is not None:
        _require(min_delay <= max_delay, "min_delay must be <= max_delay")

    return {
        "sessions": sessions,
        "proxy_mode": proxy_mode,
        "visible": bool(body.get("visible", False)),
        "continuous": bool(body.get("continuous", False)),
        "burst": bool(body.get("burst", False)),
        "min_delay": min_delay,
        "max_delay": max_delay,
    }


def _validate_business(body: dict, *, creating: bool) -> dict:
    out = {}
    if creating:
        btype = _str(body, "business_type")
        _require(btype in ("sbo", "traffic"), "business_type must be 'sbo' or 'traffic'")
        out["business_type"] = btype
        out["slug"] = _str(body, "slug", max_len=120)
        _require(bool(out["slug"]), "slug is required")
        out["name"] = _str(body, "name", max_len=200)
        _require(bool(out["name"]), "name is required")
    for key in ("name", "place_id", "search_prefix", "search_prefixes",
                "match_keywords", "exclude_keywords", "target_city"):
        if key in body:
            out[key] = _str(body, key)
    if "maps_language" in body:
        lang = _str(body, "maps_language")
        _require(lang in ("en", "ar"), "maps_language must be 'en' or 'ar'")
        out["maps_language"] = lang
    return out


# ──────────────────────────────────────────────
# Request handler
# ──────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "bot-suite/1.0"

    # quiet by default (uncomment to debug)
    def log_message(self, *args):
        pass

    # ---- response helpers ----
    def _send_json(self, obj, status: int = 200):
        payload = json.dumps(obj, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, html: str, status: int = 200):
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ---- security guard ----
    def _guard(self) -> bool:
        """Block DNS-rebinding and cross-origin requests.

        A malicious web page could otherwise point a hostname at 127.0.0.1
        (rebinding) or POST cross-origin to the local port and silently drive
        the bot. We require the Host header — and the Origin, when present — to
        resolve to a trusted local name. Responds 403 and returns False if not.
        """
        allowed = getattr(self.server, "allowed_hosts", _DEFAULT_ALLOWED_HOSTS)
        if _host_only(self.headers.get("Host", "")) not in allowed:
            self._send_json({"error": "forbidden host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and _host_only(urlparse(origin).netloc) not in allowed:
            self._send_json({"error": "forbidden origin"}, 403)
            return False
        return True

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise BadRequest("invalid JSON body")
        _require(isinstance(data, dict), "body must be a JSON object")
        return data

    # ---- GET ----
    def do_GET(self):
        if not self._guard():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                self._serve_index()
            elif path == "/api/state":
                self._send_json(service.snapshot())
            elif path == "/api/log":
                q = parse_qs(parsed.query)
                n = int((q.get("n", ["60"])[0]))
                n = max(1, min(n, 500))
                self._send_json({"log": service.tail_log(n)})
            elif (m := _BIZ_ID_RE.match(path)):
                self._business_detail(int(m.group(1)))
            else:
                self._send_json({"error": "not found"}, 404)
        except BadRequest as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---- POST ----
    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/api/run":
                self._run(body)
            elif path == "/api/stop":
                self._send_json(service.stop_bot())
            elif path == "/api/switch":
                self._switch(body)
            elif path == "/api/businesses":
                self._create(body)
            elif path == "/api/proxies/free/fetch":
                # Network-bound + slow (fetch + test). Runs synchronously.
                self._send_json(service.fetch_free_proxies())
            else:
                self._send_json({"error": "not found"}, 404)
        except BadRequest as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---- PATCH ----
    def do_PATCH(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        try:
            m = _BIZ_ID_RE.match(path)
            if not m:
                self._send_json({"error": "not found"}, 404)
                return
            body = self._read_body()
            fields = _validate_business(body, creating=False)
            if not service.update_business(int(m.group(1)), fields):
                self._send_json({"error": "business not found"}, 404)
                return
            self._send_json({"ok": True})
        except BadRequest as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---- DELETE ----
    def do_DELETE(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        try:
            m = _BIZ_ID_RE.match(path)
            if not m:
                self._send_json({"error": "not found"}, 404)
                return
            if not service.delete_business(int(m.group(1))):
                self._send_json({"error": "business not found"}, 404)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---- route bodies ----
    def _serve_index(self):
        try:
            self._send_html(INDEX_HTML.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._send_json({"error": "dashboard HTML missing (ui/static/index.html)"}, 500)

    def _run(self, body):
        args = _validate_run(body)
        result = service.start_bot(
            args["sessions"], visible=args["visible"], proxy_mode=args["proxy_mode"],
            min_delay=args["min_delay"], max_delay=args["max_delay"],
            continuous=args["continuous"], burst=args["burst"],
        )
        self._send_json(result, 200 if result.get("ok") else 409)

    def _switch(self, body):
        target = _str(body, "target", max_len=120)
        _require(bool(target), "target is required")
        if not service.switch_business(target):
            self._send_json({"error": f"business not found: {target}"}, 404)
            return
        self._send_json({"ok": True, "active": service.get_active()})

    def _create(self, body):
        payload = _validate_business(body, creating=True)
        new_id = service.create_business(payload)
        if not new_id:
            self._send_json({"error": "could not create (slug already exists?)"}, 400)
            return
        self._send_json({"ok": True, "id": new_id})

    def _business_detail(self, biz_id: int):
        biz = service.get_business_detail(biz_id)
        if not biz:
            self._send_json({"error": f"business not found: {biz_id}"}, 404)
            return
        keep = ("id", "slug", "name", "business_type") + tuple(service.EDITABLE_FIELDS)
        self._send_json({k: biz.get(k) for k in keep})


# ──────────────────────────────────────────────
# Launcher
# ──────────────────────────────────────────────

def run_server(host: str = "127.0.0.1", port: int = 8787):
    """Start the dashboard. Bound to localhost only. Ctrl+C to stop."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    # Trust loopback names plus whatever we actually bound to (e.g. a LAN IP
    # passed via --host). Any other Host/Origin is rejected (DNS-rebinding guard).
    httpd.allowed_hosts = _DEFAULT_ALLOWED_HOSTS | {host.lower()}
    print(f"\n🖥️  bot-suite dashboard → http://{host}:{port}  (local only, Ctrl+C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Dashboard closed")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
