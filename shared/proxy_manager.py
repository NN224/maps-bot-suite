"""
Proxy Manager v2
==================
Handles proxy rotation, health tracking, sticky sessions.
Uses Supabase via database.py for persistence.
"""

import random
import string
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from shared.db import get_proxy_health, upsert_proxy_health
from shared import config

logger = logging.getLogger("sbo.proxy")


class ProxyManager:
    """Manages proxy rotation with health tracking."""

    def __init__(self):
        self.session_counter = 0

    def _generate_session_id(self) -> str:
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))

    def get_proxy(self, device_type: str = "desktop") -> Optional[dict]:
        if not config.USE_PROXY:
            return None
        # PROXY_URL takes priority — works with any provider
        if getattr(config, "PROXY_URL", ""):
            return self._get_universal_proxy(config.PROXY_URL)
        if config.PROXY_PROVIDER == "brightdata":
            return self._get_brightdata_proxy(device_type)
        elif config.PROXY_PROVIDER == "soax":
            return self._get_soax_proxy(device_type)
        elif config.PROXY_PROVIDER == "custom":
            return self._get_custom_proxy()
        elif config.PROXY_PROVIDER in ("universal", "dataimpulse", "iproyal", "webshare", "smartproxy", "oxylabs"):
            return self._get_universal_proxy(getattr(config, "PROXY_URL", ""))
        else:
            logger.warning(f"Unknown proxy provider: {config.PROXY_PROVIDER}")
            return None

    def _get_universal_proxy(self, raw: str) -> Optional[dict]:
        """Parse any common proxy URL format and return Playwright proxy dict.

        A `{session}` placeholder in the URL is replaced with a fresh random
        token on every call, so each browser session gets its own sticky IP.
        """
        if not raw:
            return None
        session_id = self._generate_session_id()
        raw = raw.replace("{session}", session_id)
        parsed = self._parse_universal(raw)
        if not parsed:
            return None
        host, port, user, password, scheme = parsed
        result = {
            "server": f"{scheme}://{host}:{port}",
            "_provider": config.PROXY_PROVIDER or "universal",
            "_session_id": session_id,
        }
        if user:
            result["username"] = user
        if password:
            result["password"] = password
        return result

    @staticmethod
    def _parse_universal(raw: str):
        """Return (host, port, user, password, scheme). Supports:
        - host:port:user:pass            (DataImpulse, classic)
        - user:pass@host:port
        - scheme://user:pass@host:port
        - scheme://host:port
        """
        raw = raw.strip()
        scheme = "http"
        if "://" in raw:
            scheme, raw = raw.split("://", 1)
        if "@" in raw:
            auth, hp = raw.rsplit("@", 1)
            host, _, port = hp.partition(":")
            user, _, password = auth.partition(":")
            return host, port or "80", user, password, scheme
        parts = raw.split(":")
        if len(parts) == 4:
            host, port, user, password = parts
            return host, port, user, password, scheme
        if len(parts) == 2:
            host, port = parts
            return host, port, "", "", scheme
        logger.warning(f"Unrecognized proxy format: {raw[:40]}")
        return None

    def _get_brightdata_proxy(self, device_type: str):
        if not config.BRIGHTDATA_CUSTOMER_ID:
            return None
        session_id = self._generate_session_id()
        zone = config.BRIGHTDATA_ZONE
        username = f"brd-customer-{config.BRIGHTDATA_CUSTOMER_ID}-zone-{zone}-country-{config.TARGET_COUNTRY.lower()}-city-{config.TARGET_CITY.lower()}"
        if config.PROXY_STICKY_SESSION:
            username += f"-session-{session_id}"
        return {
            "server": f"http://{config.BRIGHTDATA_HOST}:{config.BRIGHTDATA_PORT}",
            "username": username, "password": config.BRIGHTDATA_PASSWORD,
            "_session_id": session_id, "_provider": "brightdata", "_zone": zone,
        }

    def _get_soax_proxy(self, device_type: str) -> Optional[dict]:
        if not config.SOAX_HOST:
            return None
        session_id = self._generate_session_id()
        username = config.SOAX_API_KEY
        if config.PROXY_STICKY_SESSION:
            username += f"_session-{session_id}"
        username += f"_country-{config.TARGET_COUNTRY.lower()}_city-{config.TARGET_CITY.lower()}"
        return {
            "server": f"http://{config.SOAX_HOST}:{config.SOAX_PORT}",
            "username": username, "password": "",
            "_session_id": session_id, "_provider": "soax",
        }

    def get_free_proxy(self, device_type: str = "desktop") -> Optional[dict]:
        """Pick a random working free proxy from the cached free-proxy list.

        Returns None if no free proxies have been fetched yet — the caller
        should treat that as 'no proxy available' for this session.
        """
        from shared.free_proxy import load_free_proxies
        proxies = load_free_proxies()
        if not proxies:
            logger.warning("Free-proxy mode but list is empty — fetch free proxies first.")
            return None
        chosen = random.choice(proxies)
        result = self._parse_proxy_url(chosen)
        result["_provider"] = "free"
        result["_session_id"] = self._generate_session_id()
        return result

    def _get_custom_proxy(self) -> Optional[dict]:
        if not config.CUSTOM_PROXIES:
            return None
        # Simple random selection (health check via Supabase)
        available = []
        for proxy_url in config.CUSTOM_PROXIES:
            host = self._extract_host(proxy_url)
            health = get_proxy_health(host)
            if health:
                if health.get("cooldown_until") and health["cooldown_until"] > datetime.now(timezone.utc).isoformat():
                    continue
                if (health.get("total_uses") or 0) >= config.PROXY_MAX_USES:
                    continue
            available.append(proxy_url)
        if not available:
            available = config.CUSTOM_PROXIES
        return self._parse_proxy_url(random.choice(available))

    def _parse_proxy_url(self, url: str) -> dict:
        result = {"server": url, "_provider": "custom"}
        if "@" in url:
            protocol, rest = url.split("://", 1)
            auth, host_port = rest.split("@", 1)
            result["server"] = f"{protocol}://{host_port}"
            if ":" in auth:
                result["username"], result["password"] = auth.split(":", 1)
        return result

    def _extract_host(self, url: str) -> str:
        if "@" in url:
            return url.split("@")[1].split(":")[0]
        return url.split("://")[1].split(":")[0]

    def record_result(self, proxy_config: Optional[dict], success: bool, response_ms: float = 0, blocked: bool = False):
        """Record proxy usage result — writes to Supabase."""
        if not proxy_config or not config.USE_PROXY:
            return
        provider = proxy_config.get("_provider", "unknown")
        host = proxy_config.get("server", "").replace("http://", "").replace("https://", "").split(":")[0]
        if not host or host in ("brd.superproxy.io",):
            session_id = proxy_config.get("_session_id", "unknown")
            host = f"{provider}_{session_id}"

        try:
            existing = get_proxy_health(host)
            now = datetime.now(timezone.utc).isoformat()
            if existing:
                total = (existing.get("total_uses") or 0) + 1
                succ = (existing.get("success_count") or 0) + (1 if success else 0)
                fail = (existing.get("failure_count") or 0) + (0 if success else 1)
                blk = (existing.get("blocked_count") or 0) + (1 if blocked else 0)
                avg_ms = existing.get("avg_response_ms") or 0
                if response_ms > 0:
                    avg_ms = (avg_ms * 0.8 + response_ms * 0.2) if avg_ms else response_ms
                data = {"total_uses": total, "success_count": succ, "failure_count": fail,
                        "blocked_count": blk, "avg_response_ms": avg_ms, "last_used_at": now}
                if fail > 5 and succ < fail:
                    data["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(minutes=config.PROXY_COOLDOWN_MINUTES)).isoformat()
                    data["is_active"] = False
                upsert_proxy_health(host, data)
            else:
                port_str = proxy_config.get("server", "").split(":")[-1]
                try:
                    port = int(port_str)
                except ValueError:
                    port = 0
                upsert_proxy_health(host, {
                    "provider": provider, "port": port,
                    "country": config.TARGET_COUNTRY, "city": config.TARGET_CITY,
                    "total_uses": 1, "success_count": 1 if success else 0,
                    "failure_count": 0 if success else 1, "blocked_count": 1 if blocked else 0,
                    "avg_response_ms": response_ms, "last_used_at": now, "is_active": True,
                })
        except Exception as e:
            logger.error(f"Failed to record proxy result: {e}")

    def get_health_summary(self) -> list[dict]:
        """Get proxy health summary for dashboard."""
        from shared.db import get_proxy_stats
        proxies = get_proxy_stats()
        return [{
            "host": getattr(p, 'host', ''),
            "provider": getattr(p, 'provider', ''),
            "total_uses": getattr(p, 'total_uses', 0),
            "success_rate": round((getattr(p, 'success_count', 0) or 0) / max(getattr(p, 'total_uses', 1) or 1, 1) * 100, 1),
            "blocked": getattr(p, 'blocked_count', 0),
            "avg_ms": round(getattr(p, 'avg_response_ms', 0) or 0),
            "active": getattr(p, 'is_active', True),
            "cooldown": getattr(p, 'cooldown_until', None),
        } for p in proxies]
