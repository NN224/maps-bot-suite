"""
Scenario Runner — replays a recorded Playwright scenario inside our
stealth + proxy + mobile-UA + DB-logging setup.

Usage:
    python -m scenarios.runner pr_progressive_prefix [--sessions N] [--no-proxy] [--headless]

Each scenario module must expose:
    async def run(page, *, logger, human_delay, config) -> dict
"""
import argparse
import asyncio
import importlib
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import config  # noqa: E402
from shared.fingerprint import generate_fingerprint  # noqa: E402
from shared.human_behavior import human_delay  # noqa: E402
from shared.proxy_manager import ProxyManager  # noqa: E402
from shared.db import log_session, update_session  # noqa: E402

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scenario_runner")


def _find_chromium_binary() -> Path | None:
    """Find the latest installed full Chromium binary (NOT chrome-headless-shell).

    We need this for `--visible` mode because patchright/playwright default to
    `chrome-headless-shell` which has no GUI even with headless=False.
    Returns the highest-version chromium-NNNN/ that exists.
    """
    cache = Path.home() / "Library/Caches/ms-playwright"
    if not cache.exists():
        return None
    # Skip chromium_headless_shell-* — they're headless-only
    candidates = sorted(
        (d for d in cache.glob("chromium-*") if d.is_dir() and not d.name.startswith("chromium_headless")),
        key=lambda d: int(d.name.split("-")[1]) if d.name.split("-")[1].isdigit() else 0,
        reverse=True,
    )
    for d in candidates:
        bin_path = d / "chrome-mac-arm64" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
        if bin_path.exists():
            return bin_path
    return None


CHROMIUM_PATH = _find_chromium_binary()


class ProxyDownError(RuntimeError):
    """Raised when the proxy is unreachable (auth failed, no credit, etc.).
    The runner catches this and ABORTS the entire batch — no point retrying."""


_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_AUTH",          # bad credentials OR out of credit
    "ERR_PROXY_CONNECTION",     # proxy refused
    "ERR_TUNNEL_CONNECTION",    # tunnel setup failed
    "ERR_NO_SUPPORTED_PROXIES",
    "407",                      # HTTP Proxy Authentication Required
)


async def _open_maps(page):
    """Open Google Maps + dismiss the 'Open app' prompt (mobile).

    Raises ProxyDownError if the proxy is clearly dead (auth failed, out of
    credit, etc.) — the runner will stop the whole batch in that case.
    """
    logger.info("🗺️  Opening Google Maps…")
    try:
        await page.goto(config.MAPS_URL, wait_until="commit", timeout=60000)
    except Exception as e:
        msg = str(e)
        if any(m in msg for m in _PROXY_ERROR_MARKERS):
            raise ProxyDownError(msg)
        logger.warning(f"goto warning (continuing): {e}")

    # Residential proxies are SLOW to render heavy pages. Instead of an impatient
    # body-size check, wait (up to ~90s) for the search box to actually appear —
    # that's the real "Maps is ready" signal. Reload between tries if it stalls.
    SEARCH_SEL = 'input[name="q"], [role="combobox"], #searchboxinput'
    ready = False
    for hc in range(3):
        try:
            await page.wait_for_selector(SEARCH_SEL, timeout=30000, state="visible")
            ready = True
            break
        except Exception:
            pass
        try:
            sz = await page.evaluate("() => (document.body && document.body.innerHTML.length) || 0")
        except Exception:
            sz = 0
        logger.warning(f"🩺 Maps not ready (body {sz} bytes) — reloading… ({hc + 1}/3)")
        try:
            await page.reload(wait_until="commit", timeout=60000)
        except Exception as e:
            msg = str(e)
            if any(m in msg for m in _PROXY_ERROR_MARKERS):
                raise ProxyDownError(msg)
        await page.wait_for_timeout(4000)
    if not ready:
        raise ProxyDownError("Maps search box never rendered — proxy too slow/dead")

    # Dismiss "Open Google Maps app?" prompt
    for label in [
        "Go back to web", "Keep using web", "Continue using web",
        "Use web", "Stay on web", "العودة إلى الويب", "استخدام الويب",
    ]:
        try:
            btn = page.get_by_text(label).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                logger.info(f"📱 Dismissed app prompt ({label})")
                await page.wait_for_timeout(1500)
                break
        except Exception:
            continue


async def run_one(scenario_name: str, headless: bool, no_proxy: bool,
                  session_num: int = 1, free_proxies: bool = False):
    """Run a single session of the named scenario."""
    # Import the scenario module dynamically
    module = importlib.import_module(f"scenarios.{scenario_name}")

    # Fingerprint + proxy
    fingerprint = generate_fingerprint()
    pm = ProxyManager()
    device = fingerprint.get("device_type", "tablet")
    if no_proxy:
        proxy_config = None
    elif free_proxies:
        proxy_config = pm.get_free_proxy(device)
    else:
        proxy_config = pm.get_proxy(device)

    # DB session
    db_session = log_session(
        search_prefix=config.SEARCH_PREFIX,
        search_query=f"scenario:{scenario_name}",
        status="pending",
        user_agent=fingerprint["user_agent"],
        device_type=fingerprint.get("device_type"),
        os_type=fingerprint.get("os"),
        browser_name=fingerprint.get("browser"),
        viewport_width=fingerprint["viewport"]["width"],
        viewport_height=fingerprint["viewport"]["height"],
        proxy_host=(proxy_config or {}).get("server", "direct"),
        proxy_provider=(proxy_config or {}).get("_provider", "none"),
    )

    logger.info(f"🚀 Session {session_num} (DB #{db_session.id}) — {scenario_name}")
    logger.info(f"   📱 {fingerprint['device_type']} | {fingerprint.get('os')} | {fingerprint.get('browser')}")
    logger.info(f"   🌐 Proxy: {(proxy_config or {}).get('_provider', 'direct')}")

    started = time.time()
    status = "failed"

    async with async_playwright() as pw:
        # Visible mode: use full Chromium binary so the window actually appears
        launch_args = {
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-extensions", "--no-first-run",
            ],
        }
        if not headless and CHROMIUM_PATH is not None and CHROMIUM_PATH.exists():
            launch_args["executable_path"] = str(CHROMIUM_PATH)
            launch_args["args"] += ["--window-position=80,80", "--window-size=412,915"]

        if proxy_config:
            launch_args["proxy"] = {k: v for k, v in proxy_config.items() if not k.startswith("_")}

        browser = await pw.chromium.launch(**launch_args)

        # Context with mobile UA + viewport
        locale = "en-US" if (config.MAPS_LANGUAGE or "en").lower() == "en" else "ar-AE"
        ctx = await browser.new_context(
            ignore_https_errors=True,
            user_agent=fingerprint["user_agent"],
            viewport=fingerprint["viewport"],
            locale=locale,
            timezone_id="Asia/Dubai",
            geolocation=fingerprint["geolocation"],
            permissions=["geolocation"],
            device_scale_factor=fingerprint.get("device_scale_factor", 1),
            is_mobile=fingerprint.get("is_mobile", False),
            has_touch=fingerprint.get("has_touch", False),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9,ar;q=0.6"},
        )
        # Google consent cookie
        await ctx.add_cookies([
            {"name": "CONSENT", "value": "YES+cb.20210720-07-p0.en+FX+410",
             "domain": ".google.com", "path": "/"},
            {"name": "SOCS", "value": "CAESHAgBEhJnd3NfMjAyMzAxMTAtMF9SQzIaAmFyIAEaBgiAyMqfBg",
             "domain": ".google.com", "path": "/"},
        ])

        page = await ctx.new_page()

        # Bandwidth saver: block images / media / fonts. Maps still works fully —
        # we interact with the DOM (results list, place panel, buttons), not the
        # rendered map tiles or photos. Cuts proxy data ~60-70%, which is critical
        # on metered mobile proxies. Toggle off with BLOCK_MEDIA=0.
        if os.environ.get("BLOCK_MEDIA", "1") != "0":
            async def _block_media(route):
                try:
                    if route.request.resource_type in ("image", "media", "font"):
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:
                    try:
                        await route.continue_()
                    except Exception:
                        pass
            await page.route("**/*", _block_media)

        result = {}
        try:
            await _open_maps(page)
            result = await module.run(page, logger=logger, human_delay=human_delay, config=config) or {}
            # Status logic:
            #   FAILED  = never landed on PR (engagement was skipped or hit wrong biz)
            #   SUCCESS = landed on PR + clicked directions or call (conversion signal)
            #   PARTIAL = landed on PR + some engagement but no conversion
            if not result.get("landed_on_pr", False):
                status = "failed"
            elif result.get("directions") or result.get("call"):
                status = "success"
            else:
                status = "partial"
            logger.info(f"✅ Session {session_num} done — {result}")
        except ProxyDownError as e:
            # Proxy is dead — DO NOT retry. Re-raise so the main loop aborts.
            logger.error(f"🚫 PROXY DOWN: {e}")
            status = "failed"
            # mark session as failed in DB then re-raise
            duration = round(time.time() - started, 1)
            update_session(
                db_session.id,
                status="failed",
                finished_at=datetime.now(timezone.utc),
                total_duration_seconds=duration,
                error_type="proxy_down",
                error_message=str(e)[:500],
            )
            try:
                if proxy_config:
                    pm.record_result(proxy_config, success=False)
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            raise
        except Exception as e:
            logger.error(f"💥 Session {session_num} crashed: {e}")
            status = "failed"
        finally:
            duration = round(time.time() - started, 1)
            # Persist engagement metrics from the scenario's return value
            update_session(
                db_session.id,
                status=status,
                finished_at=datetime.now(timezone.utc),
                total_duration_seconds=duration,
                dwell_time_seconds=duration,  # use total duration as dwell proxy
                autocomplete_found=bool(result.get("clicked_from_autocomplete")),
                # Reuse autocomplete_position to store WHERE we were found: the
                # AC-dropdown slot for sbo, or the results-list rank for traffic.
                autocomplete_position=result.get("rank_position"),
                business_clicked=True if status != "failed" else False,
                directions_clicked=bool(result.get("directions")),
                photos_viewed=bool(result.get("photos")),
                reviews_scrolled=bool(result.get("reviews")),
                website_clicked=bool(result.get("website")),
                call_clicked=bool(result.get("call")),
            )
            try:
                if proxy_config:
                    pm.record_result(proxy_config, success=(status == "success"))
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    return status


def _resolve_scenario(name_or_auto: str) -> str:
    """If 'auto', pick scenario based on the active business's type.
    Otherwise return the given scenario name unchanged.
    """
    if name_or_auto != "auto":
        return name_or_auto
    try:
        from shared.db import get_active_business
        biz = get_active_business()
        t = (biz or {}).get("business_type", "sbo")
        return "traffic_engage" if t == "traffic" else "pr_progressive_prefix"
    except Exception:
        return "pr_progressive_prefix"


# Minimum gap between sessions, even in back-to-back mode — gives the browser
# and its Node driver time to fully shut down (a zero gap races it → EPIPE crash).
SAFE_MIN_GAP_SECONDS = 3

# Mobile/residential proxies rotate the IP every session and an individual IP
# occasionally returns an empty page. One bad IP must NOT kill the batch — we
# only abort after this many empty-page failures IN A ROW (= proxy truly down).
MAX_CONSECUTIVE_PROXY_DOWN = 4


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", help="Scenario module name (in scenarios/) — or 'auto' to pick by active business type")
    parser.add_argument("--sessions", "-n", type=int, default=1)
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--min-delay", type=int, default=None,
                        help="Min seconds between sessions (overrides config)")
    parser.add_argument("--max-delay", type=int, default=None,
                        help="Max seconds between sessions (overrides config)")
    parser.add_argument("--continuous", action="store_true",
                        help="Run sessions back-to-back until stopped (ignores --sessions)")
    parser.add_argument("--free-proxies", action="store_true",
                        help="Use cached free public proxies instead of the configured proxy")
    args = parser.parse_args()

    scenario = _resolve_scenario(args.scenario)
    if scenario != args.scenario:
        logger.info(f"🎯 Auto-selected scenario: {scenario}")

    # Resolve the inter-session delay window (CLI overrides config).
    min_delay = args.min_delay if args.min_delay is not None \
        else getattr(config, "MIN_DELAY_BETWEEN_SESSIONS", 120)
    max_delay = args.max_delay if args.max_delay is not None \
        else getattr(config, "MAX_DELAY_BETWEEN_SESSIONS", 300)
    min_delay = max(0, min_delay)
    max_delay = max(min_delay, max_delay)

    if args.continuous:
        logger.info(f"♾️  Continuous mode — running until stopped "
                    f"(delay {min_delay}-{max_delay}s between sessions)")
    else:
        logger.info(f"🔢 Fixed batch — {args.sessions} session(s) "
                    f"(delay {min_delay}-{max_delay}s between)")

    i = 0
    consecutive_proxy_down = 0
    while True:
        i += 1
        try:
            status = await run_one(scenario, args.headless, args.no_proxy,
                                   session_num=i, free_proxies=args.free_proxies)
            consecutive_proxy_down = 0  # a healthy session resets the counter
        except ProxyDownError as e:
            consecutive_proxy_down += 1
            # One flaky IP (mobile IPs drop occasionally) must NOT kill the batch:
            # skip this session — the next one rotates to a fresh IP. Only abort
            # after several empty-page failures in a row (= proxy truly down or
            # credit exhausted). Free proxies always skip (they die constantly).
            if args.free_proxies or consecutive_proxy_down < MAX_CONSECUTIVE_PROXY_DOWN:
                logger.warning(
                    f"⚠ Empty page — skipping session, rotating IP "
                    f"({consecutive_proxy_down}/{MAX_CONSECUTIVE_PROXY_DOWN}) ({e})"
                )
                status = "failed"
            else:
                logger.error("=" * 60)
                logger.error(f"🚫 BATCH ABORTED — {MAX_CONSECUTIVE_PROXY_DOWN} proxy failures in a row")
                logger.error(f"   Reason: {e}")
                logger.error("=" * 60)
                logger.error("")
                logger.error("Common causes:")
                logger.error("  • Proxy credit/bandwidth ran out → top up / check plan")
                logger.error("  • Wrong PROXY_URL credentials in .env")
                logger.error("  • IP not whitelisted / proxy blocking your location")
                logger.error("")
                total = "∞" if args.continuous else str(args.sessions)
                logger.error(f"Sessions completed before abort: {i-1}/{total}")
                logger.error("Fix the proxy and re-run with: ./bot run N")
                sys.exit(2)  # non-zero so wrappers know it failed
        except Exception as e:
            # Any other failure (e.g. a browser/driver crash) must NOT kill the
            # whole batch — log it, mark the session failed, and move on.
            logger.error(f"💥 Session {i} crashed — skipping "
                         f"({type(e).__name__}: {str(e)[:140]})")
            status = "failed"

        # Stop after the fixed count (continuous mode never stops on its own).
        if not args.continuous and i >= args.sessions:
            break

        # Even "back-to-back" keeps a small floor so the browser/Node driver
        # fully tears down before the next launch — a zero gap races the driver
        # and crashes it (EPIPE).
        delay = random.randint(min_delay, max_delay)
        if delay >= SAFE_MIN_GAP_SECONDS:
            logger.info(f"⏰ Next session in {delay}s…")
            await asyncio.sleep(delay)
        else:
            logger.info(f"⏩ Back-to-back (min {SAFE_MIN_GAP_SECONDS}s for browser teardown)…")
            await asyncio.sleep(SAFE_MIN_GAP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
