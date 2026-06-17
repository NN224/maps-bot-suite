"""service.py — canonical operations layer for bot-suite.

Single source of truth for every action the CLI and the web dashboard can
perform: business CRUD, starting/stopping the runner, reading stats, recent
sessions, and the live log. Functions here return plain data (dicts / lists /
bools) and never print — the CLI wraps them in Rich, the web wraps them in JSON.

Everything is local: starting the bot spawns the exact same
`python -m scenarios.runner` process the CLI uses.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = "/tmp/bot_suite.log"
PID_FILE = "/tmp/bot_suite.pid"

# Editable business fields (slug + business_type are immutable after creation).
EDITABLE_FIELDS = (
    "name", "place_id", "search_prefix", "search_prefixes",
    "match_keywords", "exclude_keywords", "maps_language", "target_city",
)


# ──────────────────────────────────────────────
# Process status
# ──────────────────────────────────────────────

def is_bot_running() -> int | None:
    """Return the runner PID if a bot process is alive, else None."""
    try:
        r = subprocess.run(
            ["pgrep", "-f", "scenarios.runner"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
            return pids[0] if pids else None
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Businesses
# ──────────────────────────────────────────────

def list_all_businesses() -> list[dict]:
    """All businesses with the fields the UI needs (incl. business_type)."""
    from shared.db import _pg_exec
    try:
        return _pg_exec(
            "SELECT id, slug, name, business_type, is_active, place_id, search_prefix "
            "FROM businesses ORDER BY id",
            fetchall=True,
        ) or []
    except Exception:
        return []


def get_active() -> dict | None:
    from shared.db import get_active_business
    try:
        return get_active_business()
    except Exception:
        return None


def get_business_detail(id_or_slug: str | int) -> dict | None:
    """Full row for one business (used to pre-fill the edit form)."""
    from shared.db import get_business
    try:
        return get_business(id_or_slug)
    except Exception:
        return None


def switch_business(target: str | int) -> bool:
    """Set the active business (accepts id or slug)."""
    from shared.db import set_active_business
    try:
        return bool(set_active_business(target))
    except Exception:
        return False


def create_business(payload: dict) -> int | None:
    """Create a business. `payload` is validated by the caller (web/CLI).

    Required: business_type, name, slug. Optional: place_id, search_prefix,
    search_prefixes, match_keywords, exclude_keywords, maps_language, target_city.
    """
    from shared.db import add_business
    btype = (payload.get("business_type") or "traffic").strip()
    name = (payload.get("name") or "").strip()
    slug = (payload.get("slug") or "").strip()
    if not name or not slug:
        return None

    # traffic businesses don't need prefixes; default them like the CLI does.
    if btype == "sbo":
        prefix = (payload.get("search_prefix") or "").strip()
        prefixes = (payload.get("search_prefixes") or prefix).strip()
    else:
        prefix = "traffic"
        prefixes = "traffic"

    try:
        return add_business(
            slug=slug,
            name=name,
            place_id=(payload.get("place_id") or "").strip(),
            search_prefix=prefix,
            search_prefixes=prefixes,
            match_keywords=(payload.get("match_keywords") or name).strip(),
            exclude_keywords=(payload.get("exclude_keywords") or "").strip(),
            maps_language=(payload.get("maps_language") or "en").strip(),
            target_city=(payload.get("target_city") or "Dubai").strip(),
            business_type=btype,
        )
    except Exception:
        return None


def update_business(id_or_slug: str | int, fields: dict) -> bool:
    """Update only the provided editable fields; re-sync sbo_config if active."""
    from shared.db import (
        get_business, _pg_exec, get_active_business, set_active_business,
    )
    biz = get_business(id_or_slug)
    if not biz:
        return False

    changed = {
        k: v for k, v in fields.items()
        if k in EDITABLE_FIELDS and str(v) != str(biz.get(k) or "")
    }
    if not changed:
        return True  # nothing to do is still a success

    try:
        sets = ", ".join(f"{k} = %s" for k in changed)
        params = list(changed.values()) + [biz["id"]]
        _pg_exec(
            f"UPDATE businesses SET {sets}, updated_at = now() WHERE id = %s",
            tuple(params),
        )
        # If editing the active business, re-sync sbo_config so the next run
        # picks up the new values.
        active = get_active_business()
        if active and active["id"] == biz["id"]:
            set_active_business(biz["id"])
        return True
    except Exception:
        return False


def delete_business(id_or_slug: str | int) -> bool:
    """Remove a business row. Session history in bot_sessions is preserved."""
    from shared.db import get_business, _pg_exec
    biz = get_business(id_or_slug)
    if not biz:
        return False
    try:
        _pg_exec("DELETE FROM businesses WHERE id = %s", (biz["id"],))
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# Stats / sessions / log
# ──────────────────────────────────────────────

def today_stats() -> dict:
    """Today's aggregate stats (Dubai time). Always returns a full dict."""
    from shared.db import _pg_exec
    empty = {"total": 0, "success": 0, "partial": 0, "failed": 0,
             "ac_found": 0, "directions": 0, "calls": 0,
             "avg_dwell": None, "success_rate": 0}
    try:
        rows = _pg_exec(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
            "SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) AS partial, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN autocomplete_found THEN 1 ELSE 0 END) AS ac_found, "
            "SUM(CASE WHEN directions_clicked THEN 1 ELSE 0 END) AS directions, "
            "SUM(CASE WHEN call_clicked THEN 1 ELSE 0 END) AS calls, "
            "ROUND(AVG(CASE WHEN status<>'failed' THEN dwell_time_seconds END)::numeric, 1) AS avg_dwell "
            "FROM bot_sessions "
            "WHERE DATE(created_at AT TIME ZONE 'Asia/Dubai') = CURRENT_DATE",
            fetchall=True,
        ) or [{}]
        s = dict(rows[0]) if rows[0] else {}
        total = s.get("total") or 0
        success = s.get("success") or 0
        s["success_rate"] = round(success / total * 100) if total else 0
        # Normalise None -> 0 for the count fields.
        for k in ("total", "success", "partial", "failed", "ac_found",
                  "directions", "calls"):
            s[k] = s.get(k) or 0
        return s
    except Exception:
        return empty


def recent_sessions(limit: int = 15) -> list[dict]:
    """Latest sessions as plain dicts for the UI."""
    from shared.db import _pg_exec
    try:
        return _pg_exec(
            "SELECT id, "
            "to_char(created_at AT TIME ZONE 'Asia/Dubai','HH24:MI') AS t, "
            "search_prefix, status, "
            "autocomplete_found AS ac, directions_clicked AS dir, "
            "call_clicked AS cal, photos_viewed AS pho, "
            "ROUND(dwell_time_seconds::numeric, 0) AS dwell "
            "FROM bot_sessions ORDER BY id DESC LIMIT %s",
            (limit,), fetchall=True,
        ) or []
    except Exception:
        return []


def tail_log(n: int = 40) -> list[str]:
    """Last n lines of the live log."""
    try:
        p = Path(LOG_FILE)
        if p.exists():
            return p.read_text(errors="ignore").splitlines()[-n:]
    except Exception:
        pass
    return []


# ──────────────────────────────────────────────
# Run / stop  (spawns the exact same process the CLI uses)
# ──────────────────────────────────────────────

PROXY_MODES = ("regular", "off", "free")


def start_bot(sessions: int, *, visible: bool = False, proxy_mode: str = "regular",
              min_delay: int | None = None, max_delay: int | None = None,
              continuous: bool = False, burst: bool = False) -> dict:
    """Start a background runner batch. Returns {ok, pid} or {ok:False, error}.

    Mirrors the CLI `run` command exactly: same command, PID file, and
    append-with-header logging so history is preserved across runs.

    - proxy_mode: "regular" (configured proxy), "off" (real IP), or "free"
      (cached free public proxies — fetch them first).
    - continuous=True runs sessions back-to-back until stopped (ignores `sessions`).
    - min_delay/max_delay (seconds) override the configured inter-session wait;
      set both to 0 for true back-to-back.
    """
    existing = is_bot_running()
    if existing:
        return {"ok": False, "error": f"Bot already running (PID {existing})", "pid": existing}

    try:
        sessions = int(sessions)
    except (TypeError, ValueError):
        return {"ok": False, "error": "sessions must be an integer"}
    if sessions < 1 or sessions > 1000:
        return {"ok": False, "error": "sessions must be between 1 and 1000"}

    if proxy_mode not in PROXY_MODES:
        return {"ok": False, "error": f"proxy_mode must be one of {PROXY_MODES}"}
    if proxy_mode == "free":
        from shared.free_proxy import load_free_proxies
        if not load_free_proxies():
            return {"ok": False, "error": "No free proxies fetched yet — click 'Fetch free proxies' first"}

    # Validate the delay window if provided.
    for label, val in (("min_delay", min_delay), ("max_delay", max_delay)):
        if val is not None and (val < 0 or val > 3600):
            return {"ok": False, "error": f"{label} must be between 0 and 3600 seconds"}
    if min_delay is not None and max_delay is not None and min_delay > max_delay:
        return {"ok": False, "error": "min_delay must be <= max_delay"}

    scenario = "pr_burst" if burst else "auto"
    cmd = [sys.executable, "-m", "scenarios.runner", scenario, "--sessions", str(sessions)]
    if not visible:
        cmd.append("--headless")
    if proxy_mode == "off":
        cmd.append("--no-proxy")
    elif proxy_mode == "free":
        cmd.append("--free-proxies")
    if min_delay is not None:
        cmd += ["--min-delay", str(min_delay)]
    if max_delay is not None:
        cmd += ["--max-delay", str(max_delay)]
    if continuous:
        cmd.append("--continuous")

    biz = get_active()
    biz_name = biz["name"] if biz else "(none)"
    mode = "continuous (until stopped)" if continuous else f"{sessions} sessions"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(
                f"\n{'='*60}\n"
                f"=== Batch started {datetime.now().isoformat()} — "
                f"{mode} on {biz_name} ===\n{'='*60}\n"
            )
            f.flush()
            proc = subprocess.Popen(
                cmd, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True, cwd=str(ROOT),
            )
        Path(PID_FILE).write_text(str(proc.pid))
        return {"ok": True, "pid": proc.pid, "sessions": sessions,
                "business": biz_name, "continuous": continuous,
                "min_delay": min_delay, "max_delay": max_delay,
                "visible": visible, "proxy_mode": proxy_mode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# Free proxies (opt-in, low quality)
# ──────────────────────────────────────────────

def fetch_free_proxies() -> dict:
    """Fetch + test free public proxies and cache the working ones.

    Network-bound and slow (~10-30s). Returns the fetch summary.
    """
    try:
        from shared.free_proxy import fetch_and_store
        return fetch_and_store()
    except Exception as e:
        return {"ok": False, "error": str(e), "working": 0}


def free_proxy_status() -> dict:
    """How many free proxies are cached and how old the list is."""
    try:
        from shared.free_proxy import status
        return status()
    except Exception:
        return {"count": 0, "age_seconds": None}


def stop_bot() -> dict:
    """Stop the running bot (SIGTERM to the process group)."""
    pid = is_bot_running()
    if not pid:
        return {"ok": True, "stopped": False, "message": "Bot was not running"}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    # Clean up a stale PID file if present.
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "stopped": True, "pid": pid}


# ──────────────────────────────────────────────
# Aggregate snapshot (one call powers the whole dashboard)
# ──────────────────────────────────────────────

def snapshot(log_lines: int = 60, session_limit: int = 15) -> dict:
    """Everything the dashboard needs in a single payload."""
    pid = is_bot_running()
    active = get_active()
    return {
        "running": pid is not None,
        "pid": pid,
        "active": {
            "id": active["id"], "name": active["name"],
            "slug": active["slug"], "business_type": active["business_type"],
        } if active else None,
        "businesses": list_all_businesses(),
        "stats": today_stats(),
        "sessions": recent_sessions(session_limit),
        "log": tail_log(log_lines),
        "editable_fields": list(EDITABLE_FIELDS),
        "free_proxies": free_proxy_status(),
    }
