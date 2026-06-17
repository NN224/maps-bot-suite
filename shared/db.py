"""
Database Layer v4 — Neon Postgres + SQLite Fallback
=====================================================
Primary: Neon Postgres (when DATABASE_URL starts with postgres:// and reachable)
Fallback: SQLite (sbo_data.db) — local mirror, no external deps.

All writes go to BOTH if Postgres is available.
All reads come from SQLite (fast local) — Postgres is for cross-instance sync.
"""

import os
import sqlite3
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("sbo.db")

# ──────────────────────────────────────────────
# SQLITE SETUP
# ──────────────────────────────────────────────
_DB_PATH = Path(__file__).parent / "sbo_data.db"
_local = threading.local()  # per-thread SQLite connections


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, ensuring all tables exist."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _ensure_tables(_local.conn)
    return _local.conn


def _ensure_tables(conn: sqlite3.Connection):
    """Create any missing tables (idempotent).

    Mirrors the Neon Postgres schema so SQLite-only mode (or local fallback)
    works without errors. Postgres remains the source of truth.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sbo_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO sbo_config (key, value) VALUES
            ('business_name',    ''),
            ('search_prefix',    ''),
            ('search_prefixes',  ''),
            ('match_keywords',   ''),
            ('exclude_keywords', ''),
            ('target_city',      ''),
            ('target_country',   ''),
            ('maps_language',    'en'),
            ('geo_lat',          ''),
            ('geo_lng',          ''),
            ('use_proxy',        'false'),
            ('headless',         'true'),
            ('sessions_per_run', '5'),
            ('daily_target',     '50'),
            ('dashboard_pin',    '0000'),
            ('auto_schedule',    'false');

        CREATE TABLE IF NOT EXISTS bot_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP NOT NULL,
            finished_at TEXT,
            status TEXT DEFAULT 'pending',
            proxy_id INTEGER,
            proxy_host TEXT,
            proxy_provider TEXT,
            user_agent TEXT,
            device_type TEXT,
            viewport_width INTEGER,
            viewport_height INTEGER,
            os_type TEXT,
            browser_name TEXT,
            search_prefix TEXT NOT NULL,
            search_query TEXT,
            autocomplete_found INTEGER DEFAULT 0,
            autocomplete_position INTEGER,
            business_clicked INTEGER DEFAULT 0,
            directions_clicked INTEGER DEFAULT 0,
            photos_viewed INTEGER DEFAULT 0,
            reviews_scrolled INTEGER DEFAULT 0,
            website_clicked INTEGER DEFAULT 0,
            call_clicked INTEGER DEFAULT 0,
            dwell_time_seconds REAL DEFAULT 0,
            total_duration_seconds REAL DEFAULT 0,
            geo_lat REAL,
            geo_lng REAL,
            error_type TEXT,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON bot_sessions(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON bot_sessions(status);

        CREATE TABLE IF NOT EXISTS proxy_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            proxy_type TEXT DEFAULT 'residential',
            host TEXT NOT NULL UNIQUE,
            port INTEGER NOT NULL,
            username TEXT,
            country TEXT,
            city TEXT,
            total_uses INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            blocked_count INTEGER DEFAULT 0,
            avg_response_ms REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            cooldown_until TEXT,
            last_used_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS autocomplete_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT DEFAULT CURRENT_TIMESTAMP NOT NULL,
            search_prefix TEXT NOT NULL,
            target_business TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            suggestion_text TEXT,
            total_suggestions INTEGER,
            all_suggestions TEXT,
            check_source TEXT,
            location_name TEXT,
            device_type TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_checks_at ON autocomplete_checks(checked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_checks_prefix ON autocomplete_checks(search_prefix);

        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_sessions INTEGER DEFAULT 0,
            success_sessions INTEGER DEFAULT 0,
            partial_sessions INTEGER DEFAULT 0,
            failed_sessions INTEGER DEFAULT 0,
            autocomplete_found_count INTEGER DEFAULT 0,
            avg_position REAL,
            best_position INTEGER,
            directions_count INTEGER DEFAULT 0,
            photos_count INTEGER DEFAULT 0,
            avg_dwell_seconds REAL,
            unique_proxies_used INTEGER DEFAULT 0,
            proxy_block_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_stats(date DESC);
    """)
    conn.commit()


def _sqlite(sql: str, params=(), fetchone=False, fetchall=False, lastrowid=False):
    """Execute SQLite statement safely."""
    conn = _get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        if lastrowid:
            return cur.lastrowid
        if fetchone:
            row = cur.fetchone()
            return dict(row) if row else None
        if fetchall:
            return [dict(r) for r in cur.fetchall()]
        return cur.rowcount
    except Exception as e:
        conn.rollback()
        logger.error(f"SQLite error: {e} | SQL: {sql[:80]}")
        raise


# ──────────────────────────────────────────────
# NEON POSTGRES CLIENT (optional)
# ──────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_PG = False  # determined after connectivity check
_pg_pool = None


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _try_connect_pg() -> bool:
    """Initialize a Postgres connection pool — disable silently if unreachable."""
    global _pg_pool
    if not (DATABASE_URL and _is_postgres_url(DATABASE_URL)):
        return False
    try:
        from psycopg2 import pool as _pg_pool_mod
        _pg_pool = _pg_pool_mod.ThreadedConnectionPool(
            minconn=1, maxconn=10, dsn=DATABASE_URL,
            connect_timeout=5,
        )
        # smoke test
        conn = _pg_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            _pg_pool.putconn(conn)
        return True
    except Exception as e:
        logger.warning(f"Postgres connect failed: {e}")
        _pg_pool = None
        return False


if _try_connect_pg():
    USE_PG = True
    logger.info(f"✅ Neon Postgres connected")
else:
    logger.info("📦 Postgres offline — using SQLite only")

# Back-compat alias for server.py imports
USE_SUPABASE = USE_PG


def _pg_exec(sql: str, params=(), fetchone=False, fetchall=False, returning_id=False):
    """Execute Postgres statement via pool (with auto-reconnect on dead connection).

    Guarantees putconn() on every code path so the pool never leaks.
    """
    if not _pg_pool:
        return None
    import psycopg2
    last_err = None
    for attempt in range(2):
        conn = None
        try:
            conn = _pg_pool.getconn()
            # Health check (Neon idle-closes after ~5min)
            try:
                if conn.closed:
                    raise psycopg2.OperationalError("connection closed")
                with conn.cursor() as _ping:
                    _ping.execute("SELECT 1")
            except Exception:
                _pg_pool.putconn(conn, close=True)
                conn = _pg_pool.getconn()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if returning_id or fetchone:
                    row = cur.fetchone() if cur.description else None
                    conn.commit()
                    if not row:
                        return None
                    if returning_id:
                        return row[0]
                    cols = [d[0] for d in cur.description]
                    return dict(zip(cols, row))
                if fetchall:
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description] if cur.description else []
                    conn.commit()
                    return [dict(zip(cols, r)) for r in rows]
                conn.commit()
                return cur.rowcount
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last_err = e
            if conn is not None:
                try:
                    _pg_pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = None
            logger.info(f"PG reconnect (attempt {attempt+1}): {e}")
            continue
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.warning(f"PG error: {e} | SQL: {sql[:80]}")
            raise
        finally:
            if conn is not None:
                try:
                    _pg_pool.putconn(conn)
                except Exception:
                    pass
    if last_err:
        logger.warning(f"PG failed after retries: {last_err}")


def _pg_insert(table: str, data: dict) -> Optional[int]:
    """INSERT and return new id."""
    cols = list(data.keys())
    placeholders = ",".join(["%s"] * len(cols))
    col_names = ",".join(cols)
    sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) RETURNING id"
    return _pg_exec(sql, list(data.values()), returning_id=True)


def _pg_update(table: str, match: dict, data: dict):
    """UPDATE table SET data WHERE match."""
    sets = ", ".join(f"{k}=%s" for k in data.keys())
    where = " AND ".join(f"{k}=%s" for k in match.keys())
    sql = f"UPDATE {table} SET {sets} WHERE {where}"
    params = list(data.values()) + list(match.values())
    return _pg_exec(sql, params)


def _pg_upsert_config(key: str, value: str):
    sql = ("INSERT INTO sbo_config (key, value, updated_at) VALUES (%s, %s, now()) "
           "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=now()")
    return _pg_exec(sql, (key, value))


def _pg_upsert_proxy(host: str, data: dict):
    data = {**data, "host": host}
    cols = list(data.keys())
    placeholders = ",".join(["%s"] * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "host")
    sql = (f"INSERT INTO proxy_health ({','.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT(host) DO UPDATE SET {updates}")
    return _pg_exec(sql, list(data.values()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
_config_cache: dict = {}
_config_loaded_at: float = 0


def load_config() -> dict:
    global _config_cache, _config_loaded_at
    import time
    if _config_cache and (time.time() - _config_loaded_at) < 60:
        return _config_cache
    if USE_PG:
        try:
            rows = _pg_exec("SELECT key, value FROM sbo_config", fetchall=True) or []
            _config_cache = {r["key"]: r["value"] for r in rows}
            _config_loaded_at = time.time()
            return _config_cache
        except Exception as e:
            logger.error(f"Config load failed: {e}")
    # SQLite fallback
    try:
        rows = _sqlite("SELECT key, value FROM sbo_config", fetchall=True)
        _config_cache = {r["key"]: r["value"] for r in (rows or [])}
        _config_loaded_at = time.time()
    except Exception:
        pass
    return _config_cache


def get_config_value(key: str, default: str = "") -> str:
    return load_config().get(key, default)


def save_config(key: str, value: str):
    global _config_cache
    _sqlite(
        "INSERT INTO sbo_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    _config_cache[key] = value
    if USE_PG:
        try:
            _pg_upsert_config(key, value)
        except Exception as e:
            logger.warning(f"PG config sync failed: {e}")


def save_config_bulk(data: dict):
    for k, v in data.items():
        save_config(k, str(v))


def invalidate_config_cache():
    global _config_loaded_at
    _config_loaded_at = 0


# ──────────────────────────────────────────────
# BOT SESSIONS
# ──────────────────────────────────────────────

class SessionRow:
    """Lightweight object mimicking SQLAlchemy model for compatibility."""
    def __init__(self, data: dict):
        for k, v in data.items():
            setattr(self, k, v)
        self.id = data.get("id", 0)


def log_session(**kwargs) -> SessionRow:
    """Log a new bot session. Returns object with .id"""
    kwargs.setdefault("created_at", _now())

    # Normalize booleans for SQLite
    row_id = 0
    try:
        cols = [c for c in kwargs]
        vals = [int(v) if isinstance(v, bool) else v for v in kwargs.values()]
        placeholders = ",".join("?" * len(cols))
        col_names = ",".join(cols)
        row_id = _sqlite(
            f"INSERT INTO bot_sessions ({col_names}) VALUES ({placeholders})",
            vals, lastrowid=True
        )
        logger.debug(f"📦 SQLite session #{row_id} logged")
    except Exception as e:
        logger.error(f"SQLite log_session failed: {e}")

    # Mirror to Postgres — keep native bool/datetime (psycopg2 handles them natively)
    if USE_PG:
        try:
            pg_id = _pg_insert("bot_sessions", kwargs)
            if pg_id:
                return SessionRow({"id": pg_id, **kwargs})
        except Exception as e:
            logger.warning(f"PG log_session failed (using SQLite id): {e}")

    return SessionRow({"id": row_id, **kwargs})


def update_session(session_id: int, **kwargs):
    """Update an existing session by ID."""
    if not session_id:
        return
    # Normalize datetimes for both backends
    pg_kwargs = {}
    sqlite_kwargs = {}
    for k, v in kwargs.items():
        if isinstance(v, datetime):
            iso = v.isoformat()
            pg_kwargs[k] = iso
            sqlite_kwargs[k] = iso
        elif isinstance(v, bool):
            pg_kwargs[k] = v          # keep native bool for Postgres
            sqlite_kwargs[k] = int(v)  # SQLite stores as 0/1
        else:
            pg_kwargs[k] = v
            sqlite_kwargs[k] = v

    # SQLite update
    try:
        sets = ", ".join(f"{k}=?" for k in sqlite_kwargs)
        vals = list(sqlite_kwargs.values()) + [session_id]
        _sqlite(f"UPDATE bot_sessions SET {sets} WHERE id=?", vals)
    except Exception as e:
        logger.error(f"SQLite update_session failed: {e}")

    # Postgres mirror
    if USE_PG:
        try:
            _pg_update("bot_sessions", {"id": session_id}, pg_kwargs)
        except Exception as e:
            logger.warning(f"PG update_session failed: {e}")


def get_today_session_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        row = _sqlite(
            "SELECT COUNT(*) as cnt FROM bot_sessions WHERE created_at >= ?",
            (today,), fetchone=True
        )
        return row["cnt"] if row else 0
    except Exception:
        return 0


def get_today_stats() -> dict:
    empty = {"total": 0, "success": 0, "partial": 0, "failed": 0,
             "ac_found": 0, "avg_position": 0, "best_position": 0,
             "directions": 0, "avg_dwell": 0, "success_rate": 0}
    rows = None
    # Prefer Postgres (source of truth). "today" uses the local stats timezone.
    if USE_PG:
        try:
            rows = _pg_exec(
                "SELECT status, autocomplete_found, autocomplete_position, "
                "directions_clicked, dwell_time_seconds "
                "FROM bot_sessions "
                "WHERE DATE(created_at AT TIME ZONE %s) = CURRENT_DATE",
                ("Asia/Dubai",), fetchall=True
            )
        except Exception as e:
            logger.warning(f"PG get_today_stats failed, fallback SQLite: {e}")
    if rows is None:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            rows = _sqlite(
                "SELECT status, autocomplete_found, autocomplete_position, "
                "directions_clicked, dwell_time_seconds "
                "FROM bot_sessions WHERE created_at >= ?",
                (today,), fetchall=True
            ) or []
        except Exception as e:
            logger.error(f"get_today_stats failed: {e}")
            return empty
    try:
        if not rows:
            return empty
        total = len(rows)
        success = sum(1 for r in rows if r["status"] == "success")
        partial = sum(1 for r in rows if r["status"] == "partial")
        failed = sum(1 for r in rows if r["status"] == "failed")
        ac_found = sum(1 for r in rows if r["autocomplete_found"])
        positions = [r["autocomplete_position"] for r in rows
                     if r.get("autocomplete_position") and r["autocomplete_position"] > 0]
        directions = sum(1 for r in rows if r.get("directions_clicked"))
        dwells = [r["dwell_time_seconds"] for r in rows if r.get("dwell_time_seconds")]
        return {
            "total": total, "success": success, "partial": partial, "failed": failed,
            "ac_found": ac_found,
            "avg_position": round(sum(positions) / len(positions), 1) if positions else 0,
            "best_position": min(positions) if positions else 0,
            "directions": directions,
            "avg_dwell": round(sum(dwells) / len(dwells), 1) if dwells else 0,
            "success_rate": round(success / total * 100, 1) if total else 0,
        }
    except Exception as e:
        logger.error(f"get_today_stats failed: {e}")
        return empty


def get_recent_sessions(limit: int = 50) -> list:
    """Latest sessions — prefers Postgres (source of truth), falls back to SQLite."""
    if USE_PG:
        try:
            rows = _pg_exec(
                "SELECT * FROM bot_sessions ORDER BY created_at DESC LIMIT %s",
                (limit,), fetchall=True
            ) or []
            return [SessionRow(r) for r in rows]
        except Exception as e:
            logger.warning(f"PG get_recent_sessions failed, falling back to SQLite: {e}")
    try:
        rows = _sqlite(
            "SELECT * FROM bot_sessions ORDER BY created_at DESC LIMIT ?",
            (limit,), fetchall=True
        ) or []
        return [SessionRow(r) for r in rows]
    except Exception:
        return []


def get_session_stats_by_day(days: int = 30) -> list:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        rows = _sqlite(
            "SELECT date(created_at) as day, "
            "COUNT(*) as total, "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed, "
            "SUM(CASE WHEN autocomplete_found=1 THEN 1 ELSE 0 END) as ac_found, "
            "AVG(dwell_time_seconds) as avg_dwell "
            "FROM bot_sessions WHERE created_at >= ? GROUP BY day ORDER BY day",
            (since,), fetchall=True
        ) or []
        return [SessionRow(r) for r in rows]
    except Exception:
        return []


# ──────────────────────────────────────────────
# POSITION TRACKING
# ──────────────────────────────────────────────

def log_position_check(**kwargs) -> SessionRow:
    kwargs.setdefault("checked_at", _now())
    row_id = 0
    try:
        cols = list(kwargs.keys())
        vals = [int(v) if isinstance(v, bool) else v for v in kwargs.values()]
        placeholders = ",".join("?" * len(cols))
        row_id = _sqlite(
            f"INSERT INTO autocomplete_checks ({','.join(cols)}) VALUES ({placeholders})",
            vals, lastrowid=True
        )
    except Exception as e:
        logger.error(f"SQLite log_position_check failed: {e}")

    if USE_PG:
        try:
            pg_id = _pg_insert("autocomplete_checks", kwargs)
            if pg_id:
                return SessionRow({"id": pg_id, **kwargs})
        except Exception as e:
            logger.warning(f"PG log_position_check failed: {e}")

    return SessionRow({"id": row_id, **kwargs})


def get_position_history(prefix: str = None, days: int = 30) -> list:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        if prefix:
            rows = _sqlite(
                "SELECT * FROM autocomplete_checks WHERE checked_at >= ? AND search_prefix=? ORDER BY checked_at ASC",
                (since, prefix), fetchall=True
            )
        else:
            rows = _sqlite(
                "SELECT * FROM autocomplete_checks WHERE checked_at >= ? ORDER BY checked_at ASC",
                (since,), fetchall=True
            )
        return [SessionRow(r) for r in (rows or [])]
    except Exception:
        return []


def get_latest_position(prefix: str) -> Optional[dict]:
    """Get the most recent position check for a prefix."""
    try:
        return _sqlite(
            "SELECT position, checked_at, total_suggestions FROM autocomplete_checks "
            "WHERE search_prefix=? ORDER BY checked_at DESC LIMIT 1",
            (prefix,), fetchone=True
        )
    except Exception:
        return None


# ──────────────────────────────────────────────
# PROXY HEALTH
# ──────────────────────────────────────────────

def get_proxy_health(host: str) -> Optional[dict]:
    try:
        return _sqlite("SELECT * FROM proxy_health WHERE host=? LIMIT 1", (host,), fetchone=True)
    except Exception:
        return None


def upsert_proxy_health(host: str, data: dict):
    try:
        data["host"] = host
        data.setdefault("provider", "unknown")
        data.setdefault("port", 0)
        cols = list(data.keys())
        vals = [int(v) if isinstance(v, bool) else v for v in data.values()]
        placeholders = ",".join("?" * len(cols))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "host")
        _sqlite(
            f"INSERT INTO proxy_health ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(host) DO UPDATE SET {updates}",
            vals
        )
    except Exception as e:
        logger.error(f"upsert_proxy_health failed: {e}")

    if USE_PG:
        try:
            _pg_upsert_proxy(host, data)
        except Exception as e:
            logger.warning(f"PG upsert_proxy_health failed: {e}")


def get_proxy_stats() -> list:
    try:
        rows = _sqlite("SELECT * FROM proxy_health ORDER BY last_used_at DESC", fetchall=True) or []
        return [SessionRow(r) for r in rows]
    except Exception:
        return []


# ──────────────────────────────────────────────
# COMPATIBILITY LAYER
# ──────────────────────────────────────────────

class _FakeDB:
    def close(self): pass
    def commit(self): pass
    def rollback(self): pass
    def add(self, _): pass
    def flush(self): pass
    def refresh(self, _): pass
    def query(self, *a, **kw): return self
    def filter(self, *a, **kw): return self
    def update(self, *a, **kw): return self
    def first(self): return None
    def all(self): return []
    def scalar(self): return 0


_fake_db = _FakeDB()


def init_db():
    if USE_PG:
        logger.info("✅ Neon Postgres + SQLite ready")
    else:
        logger.info(f"📦 SQLite only: {_DB_PATH}")


def get_db():
    return _fake_db


class ProxyHealth:
    pass


class BotSession:
    pass


class AutocompleteCheck:
    pass


# ──────────────────────────────────────────────
# BUSINESSES (multi-target support)
# ──────────────────────────────────────────────

def list_businesses() -> list:
    """Return all businesses ordered by id. Postgres only."""
    if not USE_PG:
        return []
    try:
        rows = _pg_exec(
            "SELECT id, slug, name, place_id, search_prefix, is_active "
            "FROM businesses ORDER BY id", fetchall=True
        ) or []
        return rows
    except Exception as e:
        logger.error(f"list_businesses failed: {e}")
        return []


def get_business(id_or_slug) -> Optional[dict]:
    """Fetch a business by id (int) or slug (str)."""
    if not USE_PG:
        return None
    try:
        if isinstance(id_or_slug, int) or (isinstance(id_or_slug, str) and id_or_slug.isdigit()):
            sql = "SELECT * FROM businesses WHERE id = %s"
            params = (int(id_or_slug),)
        else:
            sql = "SELECT * FROM businesses WHERE slug = %s"
            params = (id_or_slug,)
        return _pg_exec(sql, params, fetchone=True)
    except Exception as e:
        logger.error(f"get_business failed: {e}")
        return None


def get_active_business() -> Optional[dict]:
    """Return the currently active business (the one the bot targets)."""
    if not USE_PG:
        return None
    try:
        return _pg_exec(
            "SELECT * FROM businesses WHERE is_active = TRUE LIMIT 1",
            fetchone=True,
        )
    except Exception as e:
        logger.error(f"get_active_business failed: {e}")
        return None


def add_business(slug: str, name: str, **fields) -> Optional[int]:
    """Create a new business. Returns new id."""
    if not USE_PG:
        return None
    cols = ["slug", "name"] + list(fields.keys())
    vals = [slug, name] + list(fields.values())
    placeholders = ", ".join(["%s"] * len(cols))
    try:
        sql = f"INSERT INTO businesses ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id"
        return _pg_exec(sql, vals, returning_id=True)
    except Exception as e:
        logger.error(f"add_business failed: {e}")
        return None


def set_active_business(id_or_slug) -> bool:
    """Mark a business active + copy its config into sbo_config so the rest
    of the bot (which reads from sbo_config) uses the right target.
    """
    biz = get_business(id_or_slug)
    if not biz:
        return False
    try:
        # Flip is_active flags atomically
        _pg_exec("UPDATE businesses SET is_active = FALSE WHERE is_active = TRUE")
        _pg_exec("UPDATE businesses SET is_active = TRUE, updated_at = now() WHERE id = %s",
                 (biz["id"],))
        # Sync to sbo_config keys (what config.py and the bot read)
        mapping = {
            "business_name": biz.get("name"),
            "place_id": biz.get("place_id"),
            "search_prefix": biz.get("search_prefix"),
            "search_prefixes": biz.get("search_prefixes"),
            "match_keywords": biz.get("match_keywords"),
            "exclude_keywords": biz.get("exclude_keywords"),
            "target_city": biz.get("target_city"),
            "target_country": biz.get("target_country"),
            "maps_language": biz.get("maps_language"),
            "geo_lat": str(biz.get("geo_lat")) if biz.get("geo_lat") is not None else None,
            "geo_lng": str(biz.get("geo_lng")) if biz.get("geo_lng") is not None else None,
        }
        for k, v in mapping.items():
            if v is None:
                continue
            _pg_exec(
                "INSERT INTO sbo_config (key, value, updated_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()",
                (k, str(v)),
            )
        invalidate_config_cache()
        return True
    except Exception as e:
        logger.error(f"set_active_business failed: {e}")
        return False


# Auto-init
init_db()
