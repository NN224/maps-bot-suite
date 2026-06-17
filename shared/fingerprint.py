"""
Fingerprint Generator
======================
Uses browserforge to generate realistic, consistent browser fingerprints.
Falls back to manual fingerprints if browserforge unavailable.
"""

import random
import logging
from typing import Optional
from shared import config

logger = logging.getLogger("sbo.fingerprint")

# Try importing browserforge
try:
    from browserforge.fingerprints import FingerprintGenerator
    HAS_BROWSERFORGE = True
    logger.info("✅ browserforge loaded")
except ImportError:
    HAS_BROWSERFORGE = False
    logger.warning("⚠️  browserforge not installed, using manual fingerprints")


# ──────────────────────────────────────────────
# DEVICE PROFILES (fallback)
# ──────────────────────────────────────────────

MOBILE_PROFILES = [
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36",
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
        "os": "android",
        "browser": "chrome",
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36",
        "viewport": {"width": 384, "height": 854},
        "device_scale_factor": 3.0,
        "is_mobile": True,
        "has_touch": True,
        "os": "android",
        "browser": "chrome",
    },
    {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 393, "height": 852},
        "device_scale_factor": 3.0,
        "is_mobile": True,
        "has_touch": True,
        "os": "ios",
        "browser": "safari",
    },
    {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/136.0.6998.60 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3.0,
        "is_mobile": True,
        "has_touch": True,
        "os": "ios",
        "browser": "chrome",
    },
]

DESKTOP_PROFILES = [
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080},
        "device_scale_factor": 1.0,
        "is_mobile": False,
        "has_touch": False,
        "os": "windows",
        "browser": "chrome",
    },
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900},
        "device_scale_factor": 2.0,
        "is_mobile": False,
        "has_touch": False,
        "os": "macos",
        "browser": "chrome",
    },
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1.0,
        "is_mobile": False,
        "has_touch": False,
        "os": "windows",
        "browser": "chrome",
    },
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
        "viewport": {"width": 1536, "height": 960},
        "device_scale_factor": 2.0,
        "is_mobile": False,
        "has_touch": False,
        "os": "macos",
        "browser": "safari",
    },
    {
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080},
        "device_scale_factor": 1.0,
        "is_mobile": False,
        "has_touch": False,
        "os": "linux",
        "browser": "chrome",
    },
]

TABLET_PROFILES = [
    {
        "user_agent": "Mozilla/5.0 (iPad; CPU OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 1024, "height": 1366},
        "device_scale_factor": 2.0,
        "is_mobile": True,
        "has_touch": True,
        "os": "ios",
        "browser": "safari",
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "viewport": {"width": 800, "height": 1280},
        "device_scale_factor": 2.0,
        "is_mobile": True,
        "has_touch": True,
        "os": "android",
        "browser": "chrome",
    },
]


def choose_device_type() -> str:
    """Choose device type based on configured distribution."""
    r = random.random()
    cumulative = 0
    for device, weight in config.DEVICE_DISTRIBUTION.items():
        cumulative += weight
        if r <= cumulative:
            return device
    return "desktop"


def generate_fingerprint(device_type: Optional[str] = None) -> dict:
    """
    Generate a complete browser fingerprint.
    Uses browserforge if available, otherwise falls back to manual profiles.

    Returns dict with keys:
        user_agent, viewport, device_scale_factor, is_mobile, has_touch,
        os, browser, device_type, geolocation, locale, timezone
    """
    # AGGRESSIVE MODE: Return SAME fingerprint every time (obvious bot pattern)
    if getattr(config, 'AGGRESSIVE_MODE', False):
        logger.warning("💀 Using STATIC fingerprint (aggressive mode)")
        return {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "viewport": {"width": 1920, "height": 1080},
            "device_scale_factor": 1.0,
            "is_mobile": False,
            "has_touch": False,
            "os": "windows",
            "browser": "chrome",
            "device_type": "desktop",
            "geolocation": {"latitude": config.GEO_CENTER_LAT, "longitude": config.GEO_CENTER_LNG},
            "locale": "en-US",
            "timezone": "Asia/Dubai",
        }

    if device_type is None:
        device_type = choose_device_type()

    # Try browserforge first
    if HAS_BROWSERFORGE and config.USE_BROWSERFORGE:
        return _browserforge_fingerprint(device_type)

    return _manual_fingerprint(device_type)


def _browserforge_fingerprint(device_type: str) -> dict:
    """Generate fingerprint using browserforge."""
    try:
        os_map = {
            "mobile": ("android",),
            "desktop": ("windows", "macos", "linux"),
            "tablet": ("android", "ios"),
        }
        
        fg = FingerprintGenerator(
            browser=("chrome",),
            os=os_map.get(device_type, ("windows",)),
        )
        fp = fg.generate()
        
        # Extract relevant fields
        navigator = fp.navigator if hasattr(fp, 'navigator') else {}
        screen = fp.screen if hasattr(fp, 'screen') else {}
        
        ua = getattr(navigator, 'userAgent', None) or fp.get('navigator', {}).get('userAgent', '')
        
        # Determine viewport from screen
        if hasattr(screen, 'width'):
            width = screen.width
            height = screen.height
        else:
            width = screen.get('width', 1920)
            height = screen.get('height', 1080)
        
        is_mobile = device_type in ("mobile", "tablet")
        
        result = {
            "user_agent": ua,
            "viewport": {"width": width, "height": height},
            "device_scale_factor": getattr(screen, 'devicePixelRatio', 1),
            "is_mobile": is_mobile,
            "has_touch": is_mobile,
            "os": _detect_os(ua),
            "browser": "chrome",
            "device_type": device_type,
            "browserforge_fp": fp,  # Keep full fingerprint for injection
        }
        
        # Add geo + locale
        _add_dubai_context(result)
        return result
        
    except Exception as e:
        logger.warning(f"browserforge failed: {e}, using manual")
        return _manual_fingerprint(device_type)


def _manual_fingerprint(device_type: str) -> dict:
    """Generate fingerprint from manual profiles."""
    if device_type == "mobile":
        profile = random.choice(MOBILE_PROFILES).copy()
    elif device_type == "tablet":
        profile = random.choice(TABLET_PROFILES).copy()
    else:
        profile = random.choice(DESKTOP_PROFILES).copy()
    
    profile["device_type"] = device_type
    
    # Slight viewport variation (+/- 0-10px)
    profile["viewport"] = {
        "width": profile["viewport"]["width"] + random.randint(-5, 5),
        "height": profile["viewport"]["height"] + random.randint(-5, 5),
    }
    
    _add_dubai_context(profile)
    return profile


def _add_dubai_context(fp: dict):
    """Add Dubai-specific geolocation and locale."""
    # Random location within Dubai (slight variation)
    fp["geolocation"] = {
        "latitude": config.GEO_CENTER_LAT + random.uniform(-0.02, 0.02),
        "longitude": config.GEO_CENTER_LNG + random.uniform(-0.02, 0.02),
        "accuracy": config.GEO_ACCURACY,
    }
    fp["timezone"] = "Asia/Dubai"
    # Respect MAPS_LANGUAGE: force matching locale to avoid Arabic UI when English requested
    _lang = (getattr(config, "MAPS_LANGUAGE", "en") or "en").lower()
    if _lang == "en":
        fp["locale"] = random.choice(["en-AE", "en-US", "en-GB"])
    elif _lang == "ar":
        fp["locale"] = random.choice(["ar-AE", "ar-SA"])
    else:
        fp["locale"] = f"{_lang}-AE"
    fp["languages"] = _get_languages(fp["locale"])


def _get_languages(locale: str) -> list:
    """Get language list based on locale."""
    if locale.startswith("ar"):
        return ["ar-AE", "ar", "en-US", "en"]
    return ["en-AE", "en-US", "en", "ar-AE", "ar"]


def _detect_os(ua: str) -> str:
    """Detect OS from user agent string."""
    ua_lower = ua.lower()
    if "android" in ua_lower:
        return "android"
    elif "iphone" in ua_lower or "ipad" in ua_lower:
        return "ios"
    elif "macintosh" in ua_lower or "mac os" in ua_lower:
        return "macos"
    elif "linux" in ua_lower:
        return "linux"
    elif "windows" in ua_lower:
        return "windows"
    return "unknown"


def get_stealth_scripts(fp: dict) -> str:
    """
    Generate JavaScript stealth patches based on fingerprint.
    Injects consistent fingerprint values.
    """
    languages = fp.get("languages", ["en-AE", "en"])
    languages_js = str(languages)
    
    return f"""
        // Core stealth patches
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
        Object.defineProperty(navigator, 'languages', {{ get: () => {languages_js} }});
        Object.defineProperty(navigator, 'platform', {{ get: () => '{_get_platform(fp)}' }});
        
        // Plugin spoofing
        Object.defineProperty(navigator, 'plugins', {{
            get: () => {{
                const plugins = [
                    {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }},
                    {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' }},
                    {{ name: 'Native Client', filename: 'internal-nacl-plugin' }},
                ];
                plugins.length = 3;
                return plugins;
            }}
        }});
        
        // Chrome runtime
        if (!window.chrome) window.chrome = {{}};
        if (!window.chrome.runtime) window.chrome.runtime = {{}};
        
        // Remove automation indicators
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        delete window.__playwright;
        delete window.__pw_manual;
        
        // Permissions API
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
            Promise.resolve({{ state: Notification.permission }}) :
            originalQuery(parameters)
        );
        
        // WebGL vendor/renderer (consistent with OS)
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return '{_get_webgl_vendor(fp)}';
            if (parameter === 37446) return '{_get_webgl_renderer(fp)}';
            return getParameter.call(this, parameter);
        }};
    """


def _get_platform(fp: dict) -> str:
    """Get navigator.platform matching OS."""
    os_type = fp.get("os", "windows")
    return {
        "windows": "Win32",
        "macos": "MacIntel",
        "linux": "Linux x86_64",
        "android": "Linux armv81",
        "ios": "iPhone",
    }.get(os_type, "Win32")


def _get_webgl_vendor(fp: dict) -> str:
    """Get WebGL vendor matching OS."""
    os_type = fp.get("os", "windows")
    return {
        "windows": "Google Inc. (NVIDIA)",
        "macos": "Google Inc. (Apple)",
        "linux": "Google Inc. (Mesa)",
        "android": "Qualcomm",
        "ios": "Apple Inc.",
    }.get(os_type, "Google Inc.")


def _get_webgl_renderer(fp: dict) -> str:
    """Get WebGL renderer matching OS."""
    os_type = fp.get("os", "windows")
    renderers = {
        "windows": [
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
        ],
        "macos": [
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)",
        ],
        "linux": [
            "ANGLE (Mesa, Mesa Intel(R) UHD Graphics 630, OpenGL 4.6)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070, OpenGL 4.6)",
        ],
        "android": [
            "Adreno (TM) 740",
            "Adreno (TM) 730",
            "Mali-G715",
        ],
        "ios": [
            "Apple GPU",
        ],
    }
    return random.choice(renderers.get(os_type, ["ANGLE (Google, Vulkan 1.3.0)"]))
