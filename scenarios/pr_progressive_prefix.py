"""
SCENARIO A — Progressive Prefix (strongest SBO signal).

Strategy (from research):
  - Type prefix CHARACTER BY CHARACTER and check autocomplete after each char
  - As soon as YOUR listing appears in dropdown → STOP TYPING and click it
  - This teaches Google: "users typing a prefix chose YOUR listing"
  - Then heavy post-click engagement (≥90s dwell + Directions + Call + Website)

All targeting (prefixes, match/exclude keywords, place id) is config-driven —
the real values come from the active business row (see shared/config.py).

Best run as DESKTOP (cleaner, more reliable than mobile).
"""
import re
import random

from scenarios._matching import build_pattern as _build_pattern, label_ok, resolve_match
from shared.human_behavior import human_click_locator, idle_mouse


# ── Fallback targeting (used only when the active business has no config) ──
# Normal operation is config-driven: prefixes, match keywords and exclude
# keywords come from the active business row in Neon (see shared/config.py).
# These defaults are intentionally EMPTY/neutral — there is no built-in target.
# Configure the real values via `./bot biz`.
_DEFAULT_PREFIXES = []
_DEFAULT_MATCH = []
_DEFAULT_EXCLUDE = []
_DEFAULT_PATTERN = re.compile(r"(?!x)x")  # matches nothing


def _targeting(config):
    """Resolve who/what to target from the active business config.

    Returns the shared match dict (pattern/match/exclude/place_id/name) plus
    `prefixes` — the progressive type ladder whose last item is the full target.
    Falls back to empty/neutral defaults if the config is empty.
    """
    t = resolve_match(
        config,
        default_match=_DEFAULT_MATCH,
        default_exclude=_DEFAULT_EXCLUDE,
        default_pattern=_DEFAULT_PATTERN,
    )
    t["prefixes"] = list(getattr(config, "SEARCH_PREFIXES", None) or []) or _DEFAULT_PREFIXES
    return t


def _label_ok(label, targeting):
    """Thin wrapper over the shared safety check (exclude wins, then match)."""
    return label_ok(label, targeting["match"], targeting["exclude"])


async def _find_search_box(page):
    """Desktop: 'Search Google Maps' combobox. Mobile: 'Find a place'.

    Generous timeouts because the page may still be settling under a slow proxy.
    """
    # Wait up to 15s for the page UI to render the search box at all
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
            if await cb.is_visible(timeout=3000):
                return cb
        except Exception:
            continue
    # Generic role=combobox (no name match)
    try:
        cb = page.locator('[role="combobox"]').first
        if await cb.is_visible(timeout=2000):
            return cb
    except Exception:
        pass
    # Direct #searchboxinput (desktop)
    try:
        cb = page.locator('#searchboxinput').first
        if await cb.is_visible(timeout=2000):
            return cb
    except Exception:
        pass
    # Mobile fallback — click the "Find a place" text to reveal combobox
    try:
        await page.get_by_text(re.compile(r"Find a place|بحث عن مكان", re.I)).first.click(timeout=3000)
        await page.wait_for_timeout(900)
        return page.locator('[role="combobox"]').first
    except Exception:
        return None


async def _check_and_click_in_autocomplete(page, logger, targeting):
    """Look for the target in the autocomplete dropdown and click it.

    Returns True on click. Logs the EXACT label/aria-label of the element it
    clicked so we can verify it really hit our business and not a sibling
    listing or competitor.
    """
    pattern = targeting["pattern"]
    # Dump what's actually in the dropdown for transparency.
    # Strip Material icon glyphs (U+E000–U+F8FF private-use area) which are
    # what `` / `` are — Google Maps icon font characters that
    # appear as garbage in our log. We want real business names only.
    try:
        items = await page.evaluate("""
            () => {
                // PUA range used by Google's icon fonts
                const stripIcons = s => (s || '').replace(/[\\uE000-\\uF8FF]/g, '').replace(/\\s+/g, ' ').trim();

                // Try multiple container selectors — pick the one that gives readable names
                const sels = [
                    '[role="option"]',          // standard ARIA
                    '[role="listbox"] [role="presentation"]',
                    '[jsname="rymPhb"]',        // Maps autocomplete rows (2024-2026)
                    'li.sbct',                  // older
                    '.suggestions-container li',
                    '[data-index]',             // data-attribute rows
                    '.sbdd_b .sbpqs_b',         // legacy
                ];
                for (const sel of sels) {
                    const els = document.querySelectorAll(sel);
                    if (!els.length) continue;
                    const out = [];
                    for (const el of els) {
                        // Prefer aria-label (clean), fall back to innerText
                        let raw = el.getAttribute('aria-label') || el.innerText || el.textContent || '';
                        raw = stripIcons(raw);
                        // Skip pure single-char remnants
                        if (raw.length >= 3) out.push(raw.slice(0, 90));
                    }
                    if (out.length) return out.slice(0, 10);
                }
                return [];
            }
        """)
        if items:
            for i, txt in enumerate(items, 1):
                logger.info(f"  📋 [{i}] {txt}")
    except Exception:
        pass

    for try_locator in [
        lambda: page.get_by_role("gridcell", name=pattern).first,
        lambda: page.get_by_role("option", name=pattern).first,
        lambda: page.locator('[role="listbox"]').get_by_text(pattern).first,
        lambda: page.locator('.sbdd_a').get_by_text(pattern).first,
        lambda: page.get_by_text(pattern).first,
    ]:
        try:
            loc = try_locator()
            if await loc.is_visible(timeout=400):
                # Capture the EXACT text we're about to click (icons stripped)
                try:
                    label = await loc.evaluate("""el => {
                        const raw = el.getAttribute('aria-label') || el.innerText || el.textContent || '';
                        return raw.replace(/[\\uE000-\\uF8FF]/g,'').replace(/\\s+/g,' ').trim().slice(0, 120);
                    }""")
                except Exception:
                    label = "(label unavailable)"
                # Safety: refuse to click unless label matches us and not a sibling
                if not _label_ok(label, targeting):
                    logger.warning(f"  ⚠️ Suggestion failed match/exclude — skipping: \"{label}\"")
                    continue
                await human_click_locator(page, loc)
                logger.info(f"  🎯 Clicked from autocomplete → \"{label}\"")
                return True
        except Exception:
            continue
    return False


async def run(page, *, logger=None, human_delay=None, config=None):
    log = (logger.info if logger else print)
    log("📋 Scenario: pr_progressive_prefix (SCENARIO A — strongest SBO)")

    # Resolve targeting from the active business config (prefixes, match/exclude
    # keywords, place_id). Falls back to legacy PR defaults if config is empty.
    targeting = _targeting(config)
    log(f"  🎯 Target: {targeting['name']}  |  prefix→'{targeting['prefixes'][-1]}'  "
        f"|  match={targeting['match']}  exclude={targeting['exclude']}")

    async def try_action(label, coro_factory, timeout=4000, optional=True):
        try:
            await coro_factory()
            log(f"  ✓ {label}")
            return True
        except Exception as e:
            lvl = "  ⚠" if optional else "  ❌"
            log(f"{lvl} {label} skipped: {str(e)[:80]}")
            return False

    # ─────────────────────────────────────────────────────────
    # PHASE 1 — Progressive prefix typing with live AC check
    # ─────────────────────────────────────────────────────────
    # Always type the FULL target progressively and click PR at the SHORTEST
    # prefix where it currently appears in autocomplete — that "ranking-edge"
    # click is the signal that pushes it toward shorter prefixes over time.
    # (Picking a random short prefix just fails the AC check and wastes the run.)
    target_prefix = targeting["prefixes"][-1]
    log(f"  → Typing full target progressively: '{target_prefix}'")

    search = await _find_search_box(page)
    if not search:
        log("  ❌ Search box not found")
        return {"scenario": "pr_progressive_prefix", "error": "no_search_box"}

    await search.click()
    await human_delay(500, 1000)

    clicked_from_autocomplete = False
    typed_so_far = ""
    for i, ch in enumerate(target_prefix):
        await page.keyboard.type(ch)
        typed_so_far += ch
        # Human inter-key delay
        await human_delay(140, 320)
        # Start checking after 2 chars
        if i >= 1:
            await page.wait_for_timeout(700)
            if await _check_and_click_in_autocomplete(page, logger, targeting):
                clicked_from_autocomplete = True
                log(f"  🎯 STRONGEST SBO SIGNAL — clicked target after typing '{typed_so_far}'")
                break

    # If still not found after typing the full prefix, do one settled check
    if not clicked_from_autocomplete:
        await human_delay(1500, 2500)
        if await _check_and_click_in_autocomplete(page, logger, targeting):
            clicked_from_autocomplete = True

    # If autocomplete never offered our listing, press Enter and click from results
    if not clicked_from_autocomplete:
        log("  ⚠ Target not in autocomplete — falling back to Enter + results")
        await page.keyboard.press("Enter")
        # Wait longer — search results page needs time under slow proxy
        await human_delay(4000, 7000)

        # Wait for the results list to actually appear (any candidate selector)
        try:
            await page.wait_for_selector(
                'a.hfpxzc, div[role="article"], .Nv2PK, a[href*="/maps/place/"]',
                timeout=15000, state="visible",
            )
            log("  ✓ Search results list ready")
        except Exception:
            log("  ⚠ Results list didn't appear within 15s")

        await human_delay(1500, 2500)  # let cards finish painting

        # Try many ways to find our listing in the results
        clicked_pr = False
        _pat = targeting["pattern"]
        for try_locator in [
            lambda: page.get_by_role("link", name=_pat).first,
            lambda: page.get_by_role("button", name=_pat).first,
            lambda: page.locator('div[role="article"]').filter(has_text=_pat).first,
            lambda: page.locator('.Nv2PK').filter(has_text=_pat).first,
            lambda: page.locator('a.hfpxzc').filter(has=page.get_by_text(_pat)).first,
            lambda: page.get_by_text(_pat).first,
        ]:
            try:
                loc = try_locator()
                if await loc.is_visible(timeout=3000):
                    # Capture label BEFORE click (icons stripped)
                    try:
                        label = await loc.evaluate("""el => {
                            const raw = el.getAttribute('aria-label') || el.innerText || el.textContent || '';
                            return raw.replace(/[\\uE000-\\uF8FF]/g,'').replace(/\\s+/g,' ').trim().slice(0, 120);
                        }""")
                    except Exception:
                        label = "(label unavailable)"
                    # Safety: refuse to click unless label matches us and not a sibling
                    if not _label_ok(label, targeting):
                        log(f"  ⚠️ Result failed match/exclude — skipping: \"{label}\"")
                        continue
                    # If we matched a container, click an inner clickable
                    try:
                        inner = loc.locator('button.hfpxzc, a[href*="/maps/place/"]').first
                        if await inner.is_visible(timeout=500):
                            await human_click_locator(page, inner)
                        else:
                            await human_click_locator(page, loc)
                    except Exception:
                        await loc.click()
                    clicked_pr = True
                    log(f"  ✓ Clicked from results → \"{label}\"")
                    break
            except Exception:
                continue

        if not clicked_pr:
            # Final fallback for mobile-ish "View list" layout
            await try_action(
                "click View list",
                lambda: page.get_by_role("button", name=re.compile(r"View list", re.I)).first.click(timeout=2500),
            )
            await human_delay(1500, 2500)
            # Try once more with the View list now open
            for try_locator in [
                lambda: page.get_by_role("button", name=_pat).first,
                lambda: page.get_by_role("link", name=_pat).first,
                lambda: page.locator('div[role="article"]').filter(has_text=_pat).first,
            ]:
                try:
                    loc = try_locator()
                    if await loc.is_visible(timeout=2500):
                        label = ""
                        try:
                            label = await loc.evaluate("""el => {
                                const raw = el.getAttribute('aria-label') || el.innerText || el.textContent || '';
                                return raw.replace(/[\\uE000-\\uF8FF]/g,'').replace(/\\s+/g,' ').trim().slice(0, 120);
                            }""")
                        except Exception:
                            pass
                        if not _label_ok(label, targeting):
                            continue
                        await loc.click()
                        clicked_pr = True
                        log(f"  ✓ Clicked after View list → \"{label}\"")
                        break
                except Exception:
                    continue

    # ── SAFETY GATE: only run engagement if we ACTUALLY clicked PR ──
    landed_on_pr = clicked_from_autocomplete or (locals().get("clicked_pr", False))
    if not landed_on_pr:
        log("  ❌ Never landed on target profile — ABORTING engagement (would hit wrong business)")
        return {
            "scenario": "pr_progressive_prefix",
            "typed_prefix": typed_so_far,
            "clicked_from_autocomplete": False,
            "landed_on_pr": False,
            "reviews": False, "photos": False, "website": False,
            "call": False, "directions": False,
        }

    # Let the profile panel load fully (with a little mouse activity)
    await idle_mouse(page, random.uniform(4, 6.5))

    # Verify the panel is actually showing OUR business (URL or title)
    try:
        url = page.url
        title = (await page.title()) or ""
        # Must match our Place ID (strongest) OR a title that passes match/exclude
        place_id = targeting["place_id"]
        url_ok = (place_id and place_id in url) or _label_ok(title, targeting)
        if not url_ok:
            log(f"  ⚠️ Page doesn't look like our listing (url={url[:80]}, title={title[:60]}) — ABORTING engagement")
            return {
                "scenario": "pr_progressive_prefix",
                "typed_prefix": typed_so_far,
                "clicked_from_autocomplete": clicked_from_autocomplete,
                "landed_on_pr": False,
                "reviews": False, "photos": False, "website": False,
                "call": False, "directions": False,
            }
        log(f"  ✓ Confirmed on target profile (Place ID or title match)")
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────
    # PHASE 2 — Heavy engagement (≥90s dwell, multiple actions)
    # ─────────────────────────────────────────────────────────
    log("  → Engagement phase")

    engagement = {"reviews": False, "photos": False, "website": False,
                  "call": False, "directions": False}

    # Read overview (always) — lifelike mouse activity instead of a frozen cursor
    log("  📖 Read overview (~10s)")
    await idle_mouse(page, random.uniform(8, 14))

    # ── Photos (70% chance — visual engagement signal)
    if random.random() < 0.7:
        clicked_photo = False
        for try_locator in [
            lambda: page.get_by_role("button", name=re.compile(r"^Photo of", re.I)).first,
            lambda: page.get_by_role("button", name=re.compile(r"See photos", re.I)).first,
            lambda: page.locator('button[jsaction*="photo"]').first,
            lambda: page.locator('img[src*="googleusercontent"]').first,
        ]:
            try:
                loc = try_locator()
                if await loc.is_visible(timeout=1200):
                    await loc.click()
                    clicked_photo = True
                    log("  ✓ open Photos")
                    break
            except Exception:
                continue
        if clicked_photo:
            engagement["photos"] = True
            await human_delay(4000, 8000)  # dwell on photo viewer
            # Swipe through 1-2 more photos
            for _ in range(random.randint(1, 2)):
                try:
                    await page.keyboard.press("ArrowRight")
                    await human_delay(2000, 4000)
                except Exception:
                    pass
            # Close photo viewer
            try:
                await page.keyboard.press("Escape")
                await human_delay(1200, 2000)
            except Exception:
                pass

    # ── Reviews tab (60% chance)
    if random.random() < 0.6:
        if await try_action(
            "open Reviews tab",
            lambda: page.get_by_role("tab", name=re.compile(r"Reviews", re.I)).first.click(timeout=2500),
        ):
            engagement["reviews"] = True
            await human_delay(4000, 8000)
            try:
                await page.evaluate("""() => {
                    const p = document.querySelector('[role="main"], .m6QErb.DxyBCb');
                    if (p) p.scrollBy({top: 600, behavior: 'smooth'});
                }""")
                await human_delay(3000, 5000)
            except Exception:
                pass
            await try_action(
                "back to Overview",
                lambda: page.get_by_role("tab", name=re.compile(r"Overview", re.I)).first.click(timeout=2500),
            )
            await human_delay(1500, 2500)

    # ── Website (20% chance — opens new tab safely)
    if random.random() < 0.2:
        if await try_action(
            "click Website",
            lambda: page.get_by_role("link", name=re.compile(r"Website|Site|الموقع", re.I)).first.click(timeout=2500),
        ):
            engagement["website"] = True
            await human_delay(6000, 11000)
            try:
                for p in page.context.pages:
                    if p is not page:
                        try: await p.close()
                        except Exception: pass
                await page.bring_to_front()
            except Exception:
                pass

    # ── Call (30% chance — high-intent signal)
    # Call element on Maps is often an <a href="tel:..."> rather than a button.
    if random.random() < 0.3:
        clicked_call = False
        for try_locator in [
            lambda: page.locator('a[data-item-id="phone:tel"]').first,
            lambda: page.locator('a[href^="tel:"]').first,
            lambda: page.get_by_role("button", name=re.compile(r"^Call ", re.I)).first,
            lambda: page.get_by_role("button", name=re.compile(r"اتصال|اتصل", re.I)).first,
            lambda: page.locator('button[data-tooltip="Copy phone number"]').first,
        ]:
            try:
                loc = try_locator()
                if await loc.is_visible(timeout=1200):
                    aria = (await loc.get_attribute("aria-label")) or ""
                    # Scope check — must mention OUR business OR be a single tel link
                    biz_name = (config.BUSINESS_NAME if config else "").lower()
                    href = (await loc.get_attribute("href")) or ""
                    if biz_name and (biz_name not in aria.lower()) and (not href.startswith("tel:")):
                        continue
                    await loc.click()
                    clicked_call = True
                    log(f"  ✓ click Call ({aria[:40] or href[:40]})")
                    break
            except Exception:
                continue
        if clicked_call:
            engagement["call"] = True
            await human_delay(2500, 4500)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await human_delay(1000, 1500)

    # ── Directions — ALWAYS (strongest conversion signal)
    if await try_action(
        "click Directions",
        lambda: page.get_by_role("button", name=re.compile(r"^Directions$", re.I)).first.click(timeout=4000),
        optional=False,
    ):
        engagement["directions"] = True
        await human_delay(6000, 10000)  # let directions panel load + dwell

    log(f"✅ Scenario done — engagement={engagement}")
    return {
        "scenario": "pr_progressive_prefix",
        "typed_prefix": typed_so_far,
        "clicked_from_autocomplete": clicked_from_autocomplete,
        "landed_on_pr": True,
        **engagement,
    }
