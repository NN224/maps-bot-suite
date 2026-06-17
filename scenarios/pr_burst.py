"""
SCENARIO — Autocomplete Burst.

Goal: teach Google the "<exact full name> -> our listing" association so our
listing gets promoted INTO the Maps autocomplete suggestions (the thing the
competitor does to surface a brand-new listing in ~48h).

Per session (kept LIGHT — no heavy engagement, to save proxy bandwidth):
  1. Type the EXACT full business name into the Maps search box (human-paced),
     so every keystroke fires an autocomplete request for that exact string.
  2. If our listing already shows in the autocomplete dropdown -> click it there
     (the strongest autocomplete signal).
  3. Otherwise press Enter, find our listing in the results and click it — this
     still reinforces the exact-query -> place association that feeds AC.
  4. Short dwell only.

Run at HIGH, concentrated volume over 24-48h on RESIDENTIAL proxies. This is a
volume game: the exact-name query + click, repeated from many fresh IPs.
"""
import random

from scenarios._matching import label_ok, resolve_match
from shared.human_behavior import human_click_locator, idle_mouse


# Neutral default burst mix used only when the active business has no config.
# Real operation is config-driven: the burst queries come from the active
# business's SEARCH_PREFIXES (see shared/config.py). Mixing several prefixes
# covers the whole funnel and looks natural (not one-query spam).
_DEFAULT_BURST_PREFIXES = [("", 1.0)]


def _burst_prefixes(config):
    """Build the weighted prefix mix from config, else the neutral default.

    Derives an even weighting from the active business's SEARCH_PREFIXES so the
    scenario stays config-driven and reveals no built-in target.
    """
    prefixes = list(getattr(config, "SEARCH_PREFIXES", None) or [])
    if not prefixes:
        return _DEFAULT_BURST_PREFIXES
    weight = 1.0 / len(prefixes)
    return [(p, weight) for p in prefixes]


def _burst_query(config):
    """Pick a search prefix by the weighted config mix (random per session)."""
    prefixes = _burst_prefixes(config)
    r = random.random()
    cum = 0.0
    for q, w in prefixes:
        cum += w
        if r <= cum:
            return q
    return prefixes[-1][0]


async def _find_search_box(page):
    # Residential proxies (esp. via a far gateway) are SLOW — Google Maps can
    # take much longer to render the search box. Be patient and retry.
    try:
        await page.wait_for_selector(
            '[role="combobox"], #searchboxinput, input[name="q"]',
            timeout=45000, state="visible",
        )
    except Exception:
        pass
    for _ in range(3):
        for sel in ('input[name="q"]', '#searchboxinput', '[role="combobox"]'):
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=4000):
                    return loc
            except Exception:
                continue
        try:
            await page.wait_for_timeout(3000)
        except Exception:
            pass
    return None


async def _click_in_autocomplete(page, targeting, log):
    """If our listing is in the AC dropdown rows, click it. Returns True/False."""
    try:
        rows = page.locator('[role="row"]')
        n = await rows.count()
    except Exception:
        n = 0
    for i in range(min(n, 12)):  # dropdown shows up to ~10; our listing may be last
        try:
            row = rows.nth(i)
            txt = (await row.inner_text()).replace("\n", " ").strip()
            if not txt or not label_ok(txt, targeting["match"], targeting["exclude"]):
                continue
            await human_click_locator(page, row)
            log(f"  🎯 Clicked from AUTOCOMPLETE → \"{txt[:70]}\"")
            return True
        except Exception:
            continue
    return False


async def _scroll_find_click(page, targeting, log):
    """Scroll the results feed and click OUR listing's link DIRECTLY by matching
    its aria-label. Never click by numeric index — Sponsored ads share the
    a.hfpxzc selector and shift positions, so index-clicks hit ads / wrong rows.
    Returns (rank, label) or (None, None)."""
    # Wait for the results list to actually render before scanning (broad
    # short queries can be slow), then scan + scroll up to 16 times.
    try:
        await page.wait_for_selector('a.hfpxzc', timeout=15000, state="visible")
    except Exception:
        pass
    last_n = -1
    stale = 0
    for _ in range(16):
        links = page.locator('a.hfpxzc')
        try:
            n = await links.count()
        except Exception:
            n = 0
        for i in range(n):
            try:
                lbl = (await links.nth(i).get_attribute('aria-label')) or ''
            except Exception:
                continue
            if "sponsored" in lbl.lower():
                continue  # never click an ad
            if label_ok(lbl, targeting["match"], targeting["exclude"]):
                el = links.nth(i)
                # Bring it fully into view first so the click can't miss a row
                # that shifted during scrolling.
                try:
                    await el.scroll_into_view_if_needed(timeout=2000)
                    await page.wait_for_timeout(300)
                except Exception:
                    pass
                await human_click_locator(page, el)
                return i + 1, lbl
        # not found yet — scroll to load more (feed, or last result into view)
        try:
            feed = page.locator('div[role="feed"]').first
            if await feed.count():
                await feed.evaluate("el => el.scrollBy(0, 1600)")
            elif n:
                await links.nth(n - 1).scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        await page.wait_for_timeout(1100)
        # stop early if the list stopped growing for 3 rounds (end reached)
        stale = stale + 1 if n == last_n else 0
        last_n = n
        if stale >= 3:
            break
    return None, None


async def run(page, *, logger=None, human_delay=None, config=None):
    log = (logger.info if logger else print)
    log("📋 Scenario: pr_burst (autocomplete burst — exact name → click)")

    targeting = resolve_match(config)
    query = _burst_query(config)
    place_id = targeting.get("place_id", "")
    log(f"  🎯 Target: {targeting['name']} | burst query: '{query}' "
        f"| match={targeting['match']} exclude={targeting['exclude']}")

    async def _wait(a, b):
        if human_delay:
            await human_delay(a, b)
        else:
            await page.wait_for_timeout((a + b) // 2)

    box = await _find_search_box(page)
    if not box:
        log("  ❌ search box not found")
        return {"scenario": "pr_burst", "landed_on_pr": False}

    # 1. Type the query char-by-char (every keystroke fires an autocomplete request)
    log("  → STEP 1: clicking the search box")
    try:
        await box.click()
    except Exception:
        pass
    await _wait(400, 900)
    log(f"  → STEP 2: typing query '{query}' character by character (human speed)")
    for ch in query:
        await page.keyboard.type(ch)
        await _wait(90, 230)
    log(f"  ⌨️  typed '{query}' — waiting ~2s for autocomplete to populate")
    await _wait(1500, 2600)

    # 2. Try the autocomplete dropdown first (strongest signal)
    log("  → STEP 3: scanning the autocomplete dropdown for our listing")
    from_ac = await _click_in_autocomplete(page, targeting, log)
    clicked = from_ac
    rank = 0 if from_ac else None  # rank 0 = found in autocomplete
    if from_ac:
        log("  ✓ our listing WAS in the dropdown — clicked it there (strongest signal)")

    # 3. Fallback: press Enter -> results list -> scroll -> click our listing
    if not clicked:
        log("  → STEP 4: not in dropdown — pressing Enter to open the results list")
        try:
            await page.keyboard.press("Enter")
            await _wait(2500, 4000)
            log("  → STEP 5: scrolling results to find our listing (skipping ads/competitors)")
            rank, lbl = await _scroll_find_click(page, targeting, log)
            if rank:
                clicked = True
                log(f"  ✓ found our listing at RANK #{rank} → \"{lbl[:50]}\" — moved mouse + clicked")
            else:
                log("  ⚠ our listing was NOT found in the results")
        except Exception as e:
            log(f"  ❌ results step failed: {str(e)[:80]}")

    if not clicked:
        log("  ✗ SESSION END: did not click our listing")
        return {"scenario": "pr_burst", "landed_on_pr": False,
                "clicked_from_autocomplete": False}

    # 4. Verify the place page actually opened (poll; the panel needs a moment)
    log("  → STEP 6: waiting for our place page to open…")
    landed, title = False, ""
    for _ in range(5):
        await _wait(1000, 1800)
        url = page.url
        try:
            title = await page.locator("h1.DUwDvf").first.inner_text(timeout=1200)
        except Exception:
            try:
                title = await page.locator("h1").first.inner_text(timeout=800)
            except Exception:
                title = ""
        panel_open = False
        try:
            panel_open = bool(await page.locator('h1.DUwDvf, button[aria-label="Directions"]').count())
        except Exception:
            panel_open = False
        if (place_id and place_id in url) or "/maps/place/" in url or \
           label_ok(title, targeting["match"], targeting["exclude"]) or panel_open:
            landed = True
            break
    if landed:
        log(f"  ✓ LANDED on our place: \"{title[:50]}\"")
    else:
        log(f"  ⚠ could not confirm the place opened (title='{title[:30]}')")

    # 5. Short human dwell (small mouse moves) — kept light to save bandwidth
    log("  → STEP 7: dwelling on the page with small mouse movements")
    try:
        await idle_mouse(page, random.uniform(4, 8))
    except Exception:
        await _wait(4000, 7000)
    log(f"  ✓ SESSION DONE — via={'autocomplete' if from_ac else f'results #{rank}'}, landed={landed}")

    return {
        "scenario": "pr_burst",
        "landed_on_pr": bool(landed),
        "clicked_from_autocomplete": bool(from_ac),
        "clicked_label": title or "(opened)",
        "rank_position": rank,
    }
