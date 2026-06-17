"""
SCENARIO: Traffic + Engagement via ORGANIC SEARCH (not direct Place ID).

For businesses where the goal is to drive TRAFFIC and conversion signals
(directions, calls, website visits) WITHOUT autocomplete-ranking games.

Why organic search and not direct Place ID URL?
  Google weights "user discovered + chose" clicks MUCH higher than direct
  navigation (which looks like a bookmark/share link hit). A direct URL hit
  to /maps/place/?q=place_id:X is treated like the user already knew the
  business — no discovery signal.

Flow:
  1. Open Google Maps (clean)
  2. Type the business name in the search box (no progressive char-check —
     traffic doesn't need to teach autocomplete; we just want a real click)
  3. Press Enter → Maps shows results
  4. Click OUR business from the list (safety check: name must match)
  5. Heavy engagement (read, photos, reviews, website, call, directions)
  6. The Place ID is used ONLY to VERIFY we landed on the right place
     (after click), never to navigate.

Fallback chain:
  - If the search results UI doesn't appear → press Enter again
  - If our business isn't in top results → as a last resort, direct Place ID
    navigation (better than failing the session)
"""
import re
import random

from scenarios._matching import label_ok, resolve_match
from shared.human_behavior import human_click_locator, idle_mouse


# How many times to scroll the results feed looking for our listing before
# giving up (a competitive query may place us several screens down).
MAX_SCROLLS = 8


async def _scan_results(page):
    """Return result-card labels in feed order (icon glyphs stripped)."""
    try:
        return await page.evaluate("""() => {
            const strip = s => (s||'').replace(/[\\uE000-\\uF8FF]/g,'').replace(/\\s+/g,' ').trim();
            return [...document.querySelectorAll('a.hfpxzc')]
                .map(c => strip(c.getAttribute('aria-label') || c.textContent || ''));
        }""")
    except Exception:
        return []


async def _scroll_feed(page):
    """Scroll the Maps results feed to load more cards."""
    try:
        await page.evaluate("""() => {
            const f = document.querySelector('[role="feed"]') || document.querySelector('.m6QErb.DxyBCb');
            if (f) f.scrollBy(0, 1400);
        }""")
    except Exception:
        pass


async def _find_listing_by_scroll(page, targeting, log, human_delay):
    """Scroll the results looking for OUR listing (match/exclude safe).

    Returns (position, label) where position is the 1-based rank in the feed,
    or (None, None) if not found after MAX_SCROLLS. The position is the ranking
    KPI — it should shrink over time if the boosting is working.
    """
    seen = 0
    for attempt in range(MAX_SCROLLS + 1):
        labels = await _scan_results(page)
        for idx, lab in enumerate(labels):
            if label_ok(lab, targeting["match"], targeting["exclude"]):
                return idx + 1, lab
        if labels and len(labels) <= seen:
            break  # no new cards loaded → stop scrolling
        seen = len(labels)
        await _scroll_feed(page)
        await human_delay(1200, 2200)
    return None, None


async def run(page, *, logger=None, human_delay=None, config=None):
    log = (logger.info if logger else print)
    log("📋 Scenario: traffic_engage (organic search → click)")

    place_id = getattr(config, "DEFAULT_PLACE_ID", "") if config else ""
    biz_name = (config.BUSINESS_NAME or "").split("|")[0].strip() if config else ""
    biz_lang = (config.MAPS_LANGUAGE or "en").lower() if config else "en"

    if not biz_name:
        log("  ❌ No business_name configured — abort")
        return {"scenario": "traffic_engage", "error": "no_business_name",
                "landed_on_pr": False, "directions": False}

    # Ranking strategy: search a SHORT competitive query (from search_prefixes)
    # and scroll to find us — a stronger "chose us over rivals" signal than
    # searching our exact name. Falls back to the business name if unset.
    targeting = resolve_match(config)
    queries = list(getattr(config, "SEARCH_PREFIXES", None) or []) if config else []
    # Pick a RANDOM query each session so 100 runs don't all search the same
    # phrase (a pattern Google would notice). All queries must still surface us.
    query = (random.choice(queries) if queries else biz_name).strip()
    log(f"  🎯 Target: {biz_name}  |  search query: '{query}'  |  match={targeting['match']} exclude={targeting['exclude']}")

    # ── 1. Maps is already open: the runner (scenarios/runner.py:_open_maps)
    #       navigates to config.MAPS_URL, runs the body health check, and
    #       dismisses the "Open app" prompt before calling this scenario's
    #       run(). Re-navigating here would open Maps twice per session
    #       (wasteful + bot-like), so we go straight to the search step.

    # ── 2. Find the search box (desktop combobox OR mobile "Find a place")
    search = None
    try:
        await page.wait_for_selector(
            '[role="combobox"], #searchboxinput, input[name="q"]',
            timeout=15000, state="visible",
        )
    except Exception:
        pass

    for name_pat in [r"Search Google Maps", r"Find a place", r"بحث"]:
        try:
            cb = page.get_by_role("combobox", name=re.compile(name_pat, re.I)).first
            if await cb.is_visible(timeout=2000):
                search = cb
                break
        except Exception:
            continue
    if not search:
        try:
            search = page.locator('[role="combobox"]').first
            if not await search.is_visible(timeout=2000):
                search = None
        except Exception:
            search = None
    if not search:
        # Mobile fallback — tap "Find a place" text to reveal combobox
        try:
            await page.get_by_text(re.compile(r"Find a place|البحث عن مكان", re.I)).first.click(timeout=3000)
            await page.wait_for_timeout(900)
            search = page.locator('[role="combobox"]').first
        except Exception:
            pass
    if not search:
        log("  ❌ Could not find search box — abort")
        return {"scenario": "traffic_engage", "landed_on_pr": False,
                "directions": False, "error": "no_search_box"}

    # ── 3. Type the search query (no progressive char-check — just realistic typing)
    log(f"  ⌨️  Typing: '{query}'")
    try:
        await search.click()
    except Exception:
        pass
    await human_delay(500, 1000)
    for ch in query:
        await page.keyboard.type(ch)
        await human_delay(80, 200)  # faster than SBO (no AC reading)
    await human_delay(1200, 2000)

    # ── 4. Submit and wait for results
    await page.keyboard.press("Enter")
    await human_delay(4000, 6500)

    # Wait for results list to actually render
    try:
        await page.wait_for_selector(
            'div[role="article"], a.hfpxzc, .Nv2PK, a[href*="/maps/place/"]',
            timeout=15000, state="visible",
        )
        log("  ✓ Search results ready")
    except Exception:
        log("  ⚠ Results list didn't appear within 15s")

    await human_delay(1500, 2500)

    # ── 5. Scroll the results to FIND our listing (ranking signal) ──
    # Scrolling past competitors to choose us on a competitive query is the
    # strongest behavioural ranking signal. We also record the position.
    clicked = False
    clicked_label = ""
    rank_position = None
    position, found_label = await _find_listing_by_scroll(page, targeting, log, human_delay)
    if position:
        rank_position = position
        log(f"  📍 Found at result position #{position} → \"{found_label}\"")
        try:
            card = page.locator('a.hfpxzc').nth(position - 1)
            await card.scroll_into_view_if_needed(timeout=4000)
            await human_delay(700, 1500)  # brief "consider" pause before clicking
            await human_click_locator(page, card)
            clicked = True
            clicked_label = found_label
            log(f"  ✓ Clicked our listing from results")
        except Exception as e:
            log(f"  ⚠ Found but click failed: {str(e)[:70]}")

    # ── 6. Last-resort fallback: direct Place ID (weaker signal but better than nothing)
    if not clicked and place_id:
        log(f"  ⚠ Not found in {MAX_SCROLLS} scrolls — falling back to direct Place ID navigation")
        try:
            await page.goto(
                f"https://www.google.com/maps/place/?q=place_id:{place_id}&hl={biz_lang}",
                wait_until="commit", timeout=30000,
            )
            await human_delay(3500, 5500)
            clicked = True
            clicked_label = f"(direct place_id fallback)"
        except Exception as e:
            log(f"  ❌ Place ID fallback also failed: {e}")
            return {"scenario": "traffic_engage", "landed_on_pr": False,
                    "directions": False, "error": "click_failed"}

    if not clicked:
        log("  ❌ Could not reach business profile")
        return {"scenario": "traffic_engage", "landed_on_pr": False,
                "directions": False, "error": "no_click"}

    await human_delay(3500, 5000)

    # ── 7. Verify URL (Place ID check)
    try:
        url = page.url
        if place_id and place_id not in url:
            log(f"  ⚠ URL doesn't contain Place ID (url={url[:80]}) — continuing anyway")
        else:
            log("  ✓ Confirmed on business profile")
    except Exception:
        pass

    # ── 8. Heavy engagement (natural human order)
    log("  → Engagement phase")

    async def try_action(label, coro_factory, timeout=3500):
        try:
            await coro_factory()
            log(f"  ✓ {label}")
            return True
        except Exception as e:
            log(f"  ⚠ {label} skipped: {str(e)[:70]}")
            return False

    engagement = {"reviews": False, "photos": False, "website": False,
                  "call": False, "directions": False}

    # 8a. Read overview (always) — with lifelike mouse activity, not a frozen cursor
    log("  📖 Read overview")
    await idle_mouse(page, random.uniform(10, 16))

    # 8b. Photos (75% chance)
    if random.random() < 0.75:
        if await try_action(
            "open Photos",
            lambda: page.get_by_role("button", name=re.compile(r"^Photo of|Photos|See photos|صور", re.I)).first.click(timeout=3000),
        ):
            engagement["photos"] = True
            await human_delay(6000, 11000)
            for _ in range(random.randint(2, 5)):
                try:
                    await page.keyboard.press("ArrowRight")
                    await human_delay(1500, 3500)
                except Exception:
                    break
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await human_delay(1500, 2500)

    # 8c. Reviews (60% chance)
    if random.random() < 0.6:
        if await try_action(
            "open Reviews tab",
            lambda: page.get_by_role("tab", name=re.compile(r"Reviews|التقييمات", re.I)).first.click(timeout=2500),
        ):
            engagement["reviews"] = True
            await human_delay(5000, 9000)
            try:
                await page.evaluate("""() => {
                    const p = document.querySelector('[role="main"], .m6QErb.DxyBCb');
                    if (p) p.scrollBy({top: 800, behavior: 'smooth'});
                }""")
                await human_delay(3000, 5000)
            except Exception:
                pass
            await try_action(
                "back to Overview",
                lambda: page.get_by_role("tab", name=re.compile(r"Overview|نظرة عامة", re.I)).first.click(timeout=2500),
            )
            await human_delay(2000, 3500)

    # 8d. Website (40% chance — strong conversion signal, opens in new tab)
    if random.random() < 0.4:
        try:
            ctx = page.context
            popup_page = None
            try:
                async with ctx.expect_page(timeout=4000) as pi:
                    # Canonical website button only (data-item-id="authority"). Google
                    # ads on the panel are also named "Website" but NEVER carry this
                    # attribute, so this guarantees we open OUR site, not an ad redirect.
                    site = page.locator('a[data-item-id="authority"]').first
                    if await site.count() == 0:
                        site = page.get_by_role("link", name=re.compile(r"Website|الموقع", re.I)).first
                    await site.click(timeout=2500)
                popup_page = await pi.value
            except Exception:
                pass

            if popup_page:
                # Let Google's /url?url= outbound redirect resolve to the real site
                # first, so we log + guard on the FINAL destination, not the wrapper.
                try:
                    await popup_page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                url = popup_page.url
                # Safety: refuse ad-redirect URLs (aclk / ad networks) AND failed
                # loads (chrome-error / about:blank / empty) — none of these are a
                # real visit to our site, so don't count them as a website visit.
                bad = (not url
                       or url.startswith(("chrome-error", "about:", "data:"))
                       or any(s in url for s in ("/aclk", "googleadservices", "doubleclick")))
                if bad:
                    log(f"  ⚠ Website not a real load — not counting ({url[:45]})")
                    try:
                        await popup_page.close()
                    except Exception:
                        pass
                    try:
                        await page.bring_to_front()
                    except Exception:
                        pass
                else:
                    log(f"  ✓ Website opened → {url[:60]}")
                    engagement["website"] = True
                    await human_delay(8000, 14000)
                    try:
                        await popup_page.mouse.wheel(0, 400)
                        await human_delay(3000, 6000)
                    except Exception:
                        pass
                    try:
                        await popup_page.close()
                    except Exception:
                        pass
                    try:
                        await page.bring_to_front()
                    except Exception:
                        pass
        except Exception as e:
            log(f"  ⚠ Website flow error: {str(e)[:60]}")

    # 8e. Call (35% chance)
    if random.random() < 0.35:
        for sel_factory in [
            lambda: page.get_by_role("button", name=re.compile(r"Call|اتصال", re.I)).first,
            lambda: page.get_by_role("link",   name=re.compile(r"Call|اتصال", re.I)).first,
            lambda: page.locator('a[data-item-id="phone"]').first,
            lambda: page.locator('a[href^="tel:"]').first,
        ]:
            try:
                el = sel_factory()
                if await el.is_visible(timeout=1500):
                    await el.click()
                    engagement["call"] = True
                    log("  ✓ Call clicked")
                    await human_delay(3000, 5000)
                    try: await page.keyboard.press("Escape")
                    except Exception: pass
                    await human_delay(1500, 2500)
                    break
            except Exception:
                continue

    # 8f. Directions — ALMOST ALWAYS (90% — the strongest conversion signal)
    if random.random() < 0.9:
        for sel_factory in [
            lambda: page.get_by_role("button", name=re.compile(r"^Directions$|الاتجاهات|اتجاهات", re.I)).first,
            lambda: page.locator('button[data-value="Directions"]').first,
            lambda: page.get_by_role("link", name=re.compile(r"^Directions$", re.I)).first,
        ]:
            try:
                el = sel_factory()
                if await el.is_visible(timeout=2500):
                    await el.click()
                    engagement["directions"] = True
                    log("  ✓ Directions clicked")
                    await human_delay(8000, 14000)
                    break
            except Exception:
                continue

    pos_note = f" | rank #{rank_position}" if rank_position else " | rank n/a (place_id fallback)"
    log(f"✅ Scenario done — engagement={engagement}{pos_note}")
    return {
        "scenario": "traffic_engage",
        "landed_on_pr": True,
        "clicked_from_autocomplete": False,
        "clicked_label": clicked_label,
        "rank_position": rank_position,
        **engagement,
    }
