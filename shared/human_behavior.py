"""
Human Behavior Simulator v2
=============================
Realistic human interaction patterns with ghost-cursor support.
Log-normal timing, Bézier mouse paths, natural scrolling.
"""

import asyncio
import random
import math
import logging
from shared import config

logger = logging.getLogger("sbo.human")

# Try ghost-cursor
try:
    from python_ghost_cursor.playwright_async import create_cursor
    HAS_GHOST_CURSOR = True
except ImportError:
    HAS_GHOST_CURSOR = False
    logger.warning("⚠️  python-ghost-cursor not installed, using basic mouse")


# ──────────────────────────────────────────────
# TIMING
# ──────────────────────────────────────────────

async def human_delay(min_ms: int = 500, max_ms: int = 2000):
    """Log-normal delay mimicking human reaction times."""
    # Aggressive mode: minimal delays
    if getattr(config, 'AGGRESSIVE_MODE', False):
        await asyncio.sleep(0.05)  # 50ms only
        return
    mu = math.log((min_ms + max_ms) / 2 / 1000)
    delay = max(min_ms / 1000, min(max_ms / 1000, random.lognormvariate(mu, 0.4)))
    await asyncio.sleep(delay)


async def think_pause():
    """Simulate a thinking pause (longer)."""
    if getattr(config, 'AGGRESSIVE_MODE', False):
        await asyncio.sleep(0.05)
        return
    await asyncio.sleep(random.uniform(1.5, 4.0))


async def micro_pause():
    """Very short pause between actions."""
    if getattr(config, 'AGGRESSIVE_MODE', False):
        return  # No pause at all
    await asyncio.sleep(random.uniform(0.1, 0.4))


# ──────────────────────────────────────────────
# TYPING
# ──────────────────────────────────────────────

async def human_type_in_focused(page, text: str, min_delay: int = 90, max_delay: int = 280):
    """
    Type into already-focused element with realistic timing.
    Includes occasional pauses, speed variation, and rare typos.
    """
    # Aggressive mode: type instantly
    if getattr(config, 'AGGRESSIVE_MODE', False):
        await page.keyboard.type(text, delay=10)  # 10ms per char
        await asyncio.sleep(0.5)  # Brief wait for autocomplete
        return

    for i, char in enumerate(text):
        # Occasional thinking pause (12% chance)
        if random.random() < 0.12 and i > 0:
            await asyncio.sleep(random.uniform(0.3, 0.8))

        # Variable speed: faster in the middle of words
        if char == ' ':
            delay = random.randint(min_delay + 50, max_delay + 100)
        elif i > 0 and i < len(text) - 1:
            delay = random.randint(min_delay - 20, max_delay - 30)
        else:
            delay = random.randint(min_delay, max_delay)

        delay = max(40, delay)  # Floor
        await page.keyboard.type(char, delay=delay)

    # Wait for autocomplete/results to load
    await human_delay(1500, 3000)


async def human_type(page, selector: str, text: str, min_delay: int = 90, max_delay: int = 280):
    """Click element then type with human timing."""
    await page.locator(selector).first.click()
    await human_delay(300, 600)
    await human_type_in_focused(page, text, min_delay, max_delay)


# ──────────────────────────────────────────────
# MOUSE & CLICKING
# ──────────────────────────────────────────────

class MouseController:
    """Handles mouse movements - uses ghost-cursor when available."""
    
    def __init__(self, page):
        self.page = page
        self.cursor = None
        if HAS_GHOST_CURSOR:
            try:
                self.cursor = create_cursor(page)
            except Exception as e:
                logger.debug(f"Ghost cursor init failed: {e}")
    
    async def click(self, selector: str, timeout: int = 10000):
        """Click element with human-like movement."""
        element = self.page.locator(selector).first
        await element.wait_for(state="visible", timeout=timeout)
        
        if self.cursor:
            try:
                await self.cursor.click(selector)
                return
            except Exception:
                pass
        
        # Fallback: manual Bézier movement
        box = await element.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
            y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
            await self._bezier_move(x, y)
            await micro_pause()
            await self.page.mouse.click(x, y)
        else:
            await element.click()
    
    async def move_random(self):
        """Random mouse movement across page."""
        vp = self.page.viewport_size or {"width": 1920, "height": 1080}
        target_x = random.randint(100, vp["width"] - 100)
        target_y = random.randint(100, vp["height"] - 100)
        
        if self.cursor:
            try:
                await self.cursor.move_to({"x": target_x, "y": target_y})
                return
            except Exception:
                pass
        
        await self._bezier_move(target_x, target_y)
    
    async def _bezier_move(self, target_x: float, target_y: float, steps: int = 0):
        """Move mouse along a Bézier curve."""
        if steps == 0:
            steps = random.randint(15, 30)
        
        # Get current position (approximate)
        vp = self.page.viewport_size or {"width": 1920, "height": 1080}
        start_x = random.randint(100, vp["width"] - 100)
        start_y = random.randint(100, vp["height"] - 100)
        
        # Control points for quadratic Bézier
        cp_x = (start_x + target_x) / 2 + random.uniform(-80, 80)
        cp_y = (start_y + target_y) / 2 + random.uniform(-80, 80)
        
        for i in range(steps):
            t = i / steps
            # Ease-in-out
            t = t * t * (3 - 2 * t)
            
            x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t ** 2 * target_x
            y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t ** 2 * target_y
            
            await self.page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.025))


async def human_click(page, selector: str, timeout: int = 10000):
    """Convenience wrapper for MouseController.click."""
    mc = MouseController(page)
    await mc.click(selector, timeout)


async def random_mouse_movement(page):
    """Convenience wrapper for random movement."""
    mc = MouseController(page)
    await mc.move_random()


async def human_click_locator(page, locator, *, timeout: int = 8000):
    """Human-like click on an already-resolved Locator.

    Scenarios resolve their own locators (with fallbacks + safety checks), so
    this complements MouseController.click() which takes a selector string.
    Moves along a path and hovers briefly before clicking instead of the instant
    Playwright teleport-click. Falls back to a plain click if geometry is gone.
    """
    if getattr(config, 'AGGRESSIVE_MODE', False):
        await locator.click()
        return
    try:
        await locator.wait_for(state="visible", timeout=timeout)
    except Exception:
        pass
    box = None
    try:
        box = await locator.bounding_box()
    except Exception:
        box = None
    if not box:
        await locator.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    mc = MouseController(page)
    if mc.cursor:
        try:
            await mc.cursor.move_to({"x": x, "y": y})
            await micro_pause()
            await page.mouse.click(x, y)
            return
        except Exception:
            pass
    await mc._bezier_move(x, y)
    await asyncio.sleep(random.uniform(0.25, 0.7))  # pre-click hover
    await page.mouse.click(x, y)


async def idle_mouse(page, duration_s: float):
    """Spend ~duration_s 'reading' — with small mouse moves / scrolls, not frozen.

    A motionless cursor for 10s of 'reading' is a strong bot tell. This keeps the
    dwell time (a real ranking signal) while adding lifelike micro-activity.
    """
    if getattr(config, 'AGGRESSIVE_MODE', False):
        await asyncio.sleep(0.05)
        return
    mc = MouseController(page)
    spent = 0.0
    while spent < duration_s:
        chunk = min(random.uniform(1.2, 3.0), duration_s - spent)
        await asyncio.sleep(chunk)
        spent += chunk
        try:
            r = random.random()
            if r < 0.5:
                await page.mouse.wheel(0, random.randint(120, 380))
            elif r < 0.85:
                await mc.move_random()
            # else: a still pause (humans pause too)
        except Exception:
            pass


# ──────────────────────────────────────────────
# SCROLLING
# ──────────────────────────────────────────────

async def human_scroll(page, direction: str = "down", amount: int = 300):
    """Smooth scroll with variable speed."""
    steps = random.randint(3, 8)
    step_amount = amount // steps
    
    for i in range(steps):
        # Variable scroll speed (faster in middle)
        speed_factor = 1.0 + 0.3 * math.sin(i / steps * math.pi)
        delta = int(step_amount * speed_factor) * (1 if direction == "down" else -1)
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(random.uniform(0.05, 0.25))


async def smooth_scroll_to(page, selector: str):
    """Scroll element into view smoothly."""
    try:
        await page.evaluate(f"""
            document.querySelector('{selector}')?.scrollIntoView({{
                behavior: 'smooth',
                block: 'center'
            }});
        """)
        await human_delay(500, 1000)
    except Exception:
        pass


# ──────────────────────────────────────────────
# ENGAGEMENT PATTERNS
# ──────────────────────────────────────────────

async def simulate_reading(page, seconds: int = 5):
    """Simulate reading a page with natural micro-interactions."""
    # Aggressive mode: just wait minimal time
    if getattr(config, 'AGGRESSIVE_MODE', False):
        await asyncio.sleep(0.5)  # Half second only!
        return
    end_time = asyncio.get_event_loop().time() + seconds
    mc = MouseController(page)
    
    while asyncio.get_event_loop().time() < end_time:
        action = random.choices(
            ["scroll", "move", "pause", "hover"],
            weights=[0.35, 0.25, 0.30, 0.10],
            k=1
        )[0]
        
        if action == "scroll":
            await human_scroll(page, "down", random.randint(80, 250))
        elif action == "move":
            await mc.move_random()
        elif action == "hover":
            # Hover over random text
            try:
                await page.evaluate("""
                    () => {
                        const texts = document.querySelectorAll('span, p, div');
                        const visible = Array.from(texts).filter(el => el.offsetParent !== null);
                        if (visible.length > 0) {
                            const el = visible[Math.floor(Math.random() * visible.length)];
                            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                        }
                    }
                """)
            except Exception:
                pass
        else:
            await human_delay(800, 2500)
        
        await human_delay(400, 1200)


async def simulate_photo_browsing(page):
    """Browse business photos with realistic timing."""
    # Aggressive mode: skip photo browsing
    if getattr(config, 'AGGRESSIVE_MODE', False):
        logger.info("📸 Photos skipped (aggressive mode)")
        return
    try:
        # Try multiple selectors for photos
        photo_selectors = [
            'button[data-value="Photos"]',
            '[aria-label*="photo" i]',
            '[aria-label*="Photo" i]',
            '[aria-label*="صور"]',
            '.ofKBgf',  # Photos tab on Maps
        ]
        
        clicked = False
        for sel in photo_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible():
                    await human_click(page, sel)
                    clicked = True
                    logger.info("📸 Opened photos")
                    break
            except Exception:
                continue
        
        if not clicked:
            # Try clicking on the main image/carousel
            try:
                main_img = page.locator('[class*="photo"], [class*="gallery"], img[src*="photo"]').first
                if await main_img.is_visible():
                    await main_img.click()
                    clicked = True
            except Exception:
                pass
        
        if not clicked:
            logger.debug("Photos section not found")
            return
        
        await human_delay(1500, 3000)
        
        # Browse 2-5 photos
        num_photos = random.randint(2, 5)
        for i in range(num_photos):
            # Scroll through or click next
            await human_scroll(page, "down", random.randint(200, 400))
            await human_delay(1500, 3500)
            
            # Sometimes pause longer on a photo (reading/looking)
            if random.random() < 0.3:
                await human_delay(2000, 4000)
        
        logger.info(f"📸 Browsed {num_photos} photos")
        
    except Exception as e:
        logger.debug(f"Photo browsing failed: {e}")


async def simulate_reviews_scroll(page):
    """Scroll through reviews section."""
    # Aggressive mode: skip reviews
    if getattr(config, 'AGGRESSIVE_MODE', False):
        logger.info("📝 Reviews skipped (aggressive mode)")
        return
    try:
        review_selectors = [
            'button[data-value="Reviews"]',
            '[aria-label*="review" i]',
            '[aria-label*="مراجع"]',
            '.hqzQac',
        ]
        
        for sel in review_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible():
                    await human_click(page, sel)
                    logger.info("📝 Opened reviews")
                    await human_delay(1500, 2500)
                    
                    # Scroll through 2-4 reviews
                    for _ in range(random.randint(2, 4)):
                        await human_scroll(page, "down", random.randint(200, 400))
                        await human_delay(2000, 4000)
                    
                    return True
            except Exception:
                continue
        
        return False
    except Exception:
        return False
