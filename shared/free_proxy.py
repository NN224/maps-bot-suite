"""free_proxy.py — fetch & test free public proxies (opt-in, low quality).

Pulls candidate proxies from public sources, tests which ones can actually
reach Google, and caches the working ones to a file the runner reads when
launched in free-proxy mode.

Uses ONLY the Python standard library (urllib) — the venv's requests/httpx are
broken on Python 3.14, and stdlib has no such problem.

⚠️  Free public proxies are datacenter IPs shared by thousands of users and are
mostly dead/flagged. This is a best-effort, low-reliability option — see the
dashboard warning. The mobile/residential proxy is far better for Google Maps.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger("sbo.freeproxy")

FREE_PROXY_FILE = Path("/tmp/bot_free_proxies.json")
# Reaching Google with a 204 is exactly the capability the bot needs.
TEST_URL = "http://www.google.com/generate_204"
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36")}

GEONODE_URL = (
    "https://proxylist.geonode.com/api/proxy-list"
    "?limit=200&page=1&sort_by=lastChecked&sort_type=desc&protocols=http"
)
# Plain-text "ip:port" lists on GitHub raw — reliable (no anti-bot / TLS
# fingerprinting), unlike proxyscrape which resets stdlib connections.
GITHUB_LISTS = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]


def _http_get(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _fetch_candidates(limit: int) -> list[str]:
    """Collect candidate proxy URLs (http://ip:port) from public sources."""
    out: list[str] = []

    # Source 1: geonode (JSON) — reliable from stdlib.
    try:
        data = json.loads(_http_get(GEONODE_URL))
        for p in data.get("data", []):
            ip, port = p.get("ip"), p.get("port")
            if ip and port:
                out.append(f"http://{ip}:{port}")
    except Exception as e:
        logger.warning(f"geonode fetch failed: {e}")

    # Source 2: GitHub raw lists (plain "ip:port") — reliable supplement.
    for url in GITHUB_LISTS:
        try:
            for line in _http_get(url).splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                out.append(line if line.startswith("http") else "http://" + line)
        except Exception as e:
            logger.warning(f"github list fetch failed ({url.rsplit('/', 3)[1]}): {e}")

    # Dedup, preserve order, cap.
    seen, dedup = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup[:limit]


def _test_one(proxy: str, timeout: int = 6) -> str | None:
    """Return the proxy if it can reach Google through it, else None."""
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        req = urllib.request.Request(TEST_URL, headers=_UA)
        with opener.open(req, timeout=timeout) as r:
            return proxy if r.status in (200, 204) else None
    except Exception:
        return None


def fetch_and_store(candidate_limit: int = 80, test: bool = True,
                    max_workers: int = 25) -> dict:
    """Fetch candidates, test them in parallel, save the working ones.

    Returns {ok, candidates, tested, working} or {ok:False, error}.
    """
    candidates = _fetch_candidates(candidate_limit)
    if not candidates:
        return {"ok": False, "error": "no proxies returned from free sources",
                "working": 0}

    working = candidates
    tested = 0
    if test:
        working = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_test_one, p) for p in candidates]
            for fut in as_completed(futures):
                tested += 1
                res = fut.result()
                if res:
                    working.append(res)

    data = {"fetched_at": time.time(), "proxies": working}
    try:
        FREE_PROXY_FILE.write_text(json.dumps(data))
    except Exception as e:
        return {"ok": False, "error": f"could not save list: {e}",
                "working": len(working)}

    return {"ok": True, "candidates": len(candidates),
            "tested": tested, "working": len(working)}


def load_free_proxies() -> list[str]:
    """Working free proxies cached from the last fetch."""
    try:
        if FREE_PROXY_FILE.exists():
            return json.loads(FREE_PROXY_FILE.read_text()).get("proxies", [])
    except Exception:
        pass
    return []


def status() -> dict:
    """How many free proxies are cached and how old the list is."""
    try:
        if FREE_PROXY_FILE.exists():
            d = json.loads(FREE_PROXY_FILE.read_text())
            return {
                "count": len(d.get("proxies", [])),
                "age_seconds": int(time.time() - d.get("fetched_at", 0)),
            }
    except Exception:
        pass
    return {"count": 0, "age_seconds": None}
