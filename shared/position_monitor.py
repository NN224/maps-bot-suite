"""
Position Monitor v2
=====================
Tracks autocomplete position using 3 sources:
1. Google Places API (most accurate - same API Maps uses) ← Cloud Console
2. Google direct endpoint (free, less accurate)
3. SerpApi (paid alternative)

The Places API is THE source of truth — it's literally the same
autocomplete that real users see on Google Maps.
"""

import json
import logging
import random
import time
from datetime import datetime
import requests
from shared import config
from shared.db import log_position_check
from shared.utils import is_target_match

logger = logging.getLogger("sbo.monitor")


class PositionMonitor:
    """Monitors autocomplete position for target business across multiple sources."""

    def __init__(self):
        self.last_check = {}
        self.history = []

    def check_all_prefixes(self, source: str = "auto") -> list[dict]:
        """Check position for all configured prefixes."""
        results = []
        for prefix in config.SEARCH_PREFIXES:
            result = self.check_position(prefix, source=source)
            results.append(result)
            time.sleep(random.uniform(1, 3))
        return results

    def check_position(self, prefix: str, source: str = "auto") -> dict:
        """
        Check if target business appears in autocomplete for given prefix.
        
        Sources (priority):
        1. google_places — Places API Autocomplete (most accurate, ~$0.003/request)
        2. direct — Google's free endpoint (rate-limited, less Maps-specific)
        3. serpapi — SerpApi Maps Autocomplete ($75/mo)
        """
        if source == "auto":
            if config.GOOGLE_PLACES_API_KEY:
                result = self._check_google_places(prefix)
            elif config.USE_SERPAPI and config.SERPAPI_KEY:
                result = self._check_serpapi(prefix)
            else:
                result = self._check_direct(prefix)
        elif source == "google_places":
            result = self._check_google_places(prefix)
        elif source == "serpapi":
            result = self._check_serpapi(prefix)
        else:
            result = self._check_direct(prefix)

        # Log to database
        try:
            log_position_check(
                search_prefix=prefix,
                target_business=config.BUSINESS_NAME,
                position=result["position"],
                suggestion_text=result.get("matched_text"),
                total_suggestions=result.get("total"),
                all_suggestions=json.dumps(result.get("suggestions", []), ensure_ascii=False),
                check_source=result.get("source", "unknown"),
                location_name=f"{config.TARGET_CITY}, {config.TARGET_COUNTRY}",
            )
        except Exception as e:
            logger.error(f"Failed to log position check: {e}")

        self.last_check[prefix] = result
        self.history.append(result)
        
        pos = result['position']
        if pos > 0:
            logger.info(f"🎯 Prefix '{prefix}': FOUND at position {pos} — \"{result.get('matched_text', '')}\"")
        else:
            logger.info(f"⚪ Prefix '{prefix}': NOT FOUND ({result['total']} suggestions)")
        
        return result

    # ──────────────────────────────────────────────
    # SOURCE 1: Google Places API (BEST)
    # ──────────────────────────────────────────────

    def _check_google_places(self, prefix: str) -> dict:
        """
        Check via Google Places API Autocomplete (New).
        This is THE most accurate check — it's the exact same API
        that Google Maps uses to show autocomplete suggestions.
        
        Cost: ~$2.83 per 1000 requests (Autocomplete - Per Session)
        Docs: https://developers.google.com/maps/documentation/places/web-service/autocomplete
        """
        try:
            # New Places API (recommended)
            url = "https://places.googleapis.com/v1/places:autocomplete"
            
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
            }
            
            body = {
                "input": prefix,
                "locationBias": {
                    "circle": {
                        "center": {
                            "latitude": config.GEO_CENTER_LAT,
                            "longitude": config.GEO_CENTER_LNG,
                        },
                        "radius": 5000.0  # 5km radius around Dubai center
                    }
                },
                "languageCode": "ar",
                "regionCode": "AE",
                "includedPrimaryTypes": ["establishment"],  # Business results only
            }
            
            resp = requests.post(url, headers=headers, json=body, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            suggestions_raw = data.get("suggestions", [])
            suggestions = []
            matched_text = None
            position = 0
            
            for i, s in enumerate(suggestions_raw, 1):
                place = s.get("placePrediction", {})
                text_obj = place.get("text", {})
                text = text_obj.get("text", "")
                place_id = place.get("placeId", "")
                structured = place.get("structuredFormat", {})
                main_text = structured.get("mainText", {}).get("text", "")
                secondary = structured.get("secondaryText", {}).get("text", "")
                
                suggestions.append({
                    "text": text,
                    "main_text": main_text,
                    "secondary_text": secondary,
                    "place_id": place_id,
                    "position": i,
                })
                
                # Check if this is our target
                combined = f"{text} {main_text}".lower()
                if is_target_match(combined):
                    position = i
                    matched_text = text
            
            suggestion_texts = [s["text"] for s in suggestions]
            
            return {
                "prefix": prefix,
                "position": position,
                "matched_text": matched_text,
                "total": len(suggestions),
                "suggestions": suggestion_texts,
                "suggestions_detailed": suggestions,
                "source": "google_places",
                "timestamp": datetime.utcnow().isoformat(),
            }
            
        except requests.exceptions.HTTPError as e:
            error_detail = ""
            try:
                error_detail = e.response.json().get("error", {}).get("message", str(e))
            except Exception:
                error_detail = str(e)
            logger.error(f"Google Places API error: {error_detail}")
            
            # Fallback to direct method
            logger.info("Falling back to direct endpoint...")
            return self._check_direct(prefix)
            
        except Exception as e:
            logger.error(f"Google Places check failed: {e}")
            return self._check_direct(prefix)

    # ──────────────────────────────────────────────
    # SOURCE 2: Google Direct (FREE)
    # ──────────────────────────────────────────────

    def _check_direct(self, prefix: str) -> dict:
        """Check via Google's free autocomplete endpoint (Web Search, not Maps-specific)."""
        try:
            params = {
                "q": prefix,
                "gl": "ae",
                "hl": "ar",
                "client": "chrome",
                "ds": "",
            }
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "ar-AE,ar;q=0.9,en;q=0.8",
            }
            
            resp = requests.get(
                config.AUTOCOMPLETE_ENDPOINT,
                params=params,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            
            data = resp.json()
            suggestions = data[1] if len(data) > 1 else []
            
            for i, text in enumerate(suggestions, 1):
                if is_target_match(text):
                    return {
                        "prefix": prefix,
                        "position": i,
                        "matched_text": text,
                        "total": len(suggestions),
                        "suggestions": suggestions,
                        "source": "direct",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
            
            return {
                "prefix": prefix,
                "position": 0,
                "matched_text": None,
                "total": len(suggestions),
                "suggestions": suggestions,
                "source": "direct",
                "timestamp": datetime.utcnow().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Direct autocomplete check failed: {e}")
            return self._error_result(prefix, "direct", str(e))

    # ──────────────────────────────────────────────
    # SOURCE 3: SerpApi (PAID)
    # ──────────────────────────────────────────────

    def _check_serpapi(self, prefix: str) -> dict:
        """Check via SerpApi Google Maps Autocomplete."""
        try:
            params = {
                "engine": "google_maps_autocomplete",
                "q": prefix,
                "ll": f"@{config.GEO_CENTER_LAT},{config.GEO_CENTER_LNG},14z",
                "hl": "ar",
                "gl": "ae",
                "api_key": config.SERPAPI_KEY,
            }
            
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            suggestions = data.get("suggestions", [])
            suggestion_texts = [s.get("value", "") for s in suggestions]
            
            for i, s in enumerate(suggestions, 1):
                value = s.get("value", "").lower()
                if is_target_match(value):
                    return {
                        "prefix": prefix,
                        "position": i,
                        "matched_text": s.get("value", ""),
                        "total": len(suggestions),
                        "suggestions": suggestion_texts,
                        "source": "serpapi",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
            
            return {
                "prefix": prefix,
                "position": 0,
                "matched_text": None,
                "total": len(suggestions),
                "suggestions": suggestion_texts,
                "source": "serpapi",
                "timestamp": datetime.utcnow().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"SerpApi check failed: {e}")
            return self._error_result(prefix, "serpapi", str(e))

    # ──────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────

    def _error_result(self, prefix: str, source: str, error: str) -> dict:
        return {
            "prefix": prefix,
            "position": -1,
            "matched_text": None,
            "total": 0,
            "suggestions": [],
            "source": source,
            "error": error,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def get_latest_positions(self) -> dict:
        return {prefix: data.get("position", -1) for prefix, data in self.last_check.items()}

    def get_trend(self, prefix: str, days: int = 7) -> dict:
        from shared.db import get_position_history
        checks = get_position_history(prefix=prefix, days=days)
        if not checks:
            return {"trend": "no_data", "avg": 0, "best": 0, "worst": 0, "found_pct": 0}
        
        positions = [c.position for c in checks if c.position and c.position > 0]
        total = len(checks)
        found = len(positions)
        
        return {
            "trend": self._calc_trend(positions),
            "avg": round(sum(positions) / len(positions), 1) if positions else 0,
            "best": min(positions) if positions else 0,
            "worst": max(positions) if positions else 0,
            "found_pct": round(found / total * 100, 1) if total else 0,
            "total_checks": total,
        }

    def _calc_trend(self, positions: list) -> str:
        if len(positions) < 4:
            return "insufficient_data"
        half = len(positions) // 2
        first_half_avg = sum(positions[:half]) / half
        second_half_avg = sum(positions[half:]) / (len(positions) - half)
        if second_half_avg < first_half_avg - 0.5:
            return "improving"
        elif second_half_avg > first_half_avg + 0.5:
            return "declining"
        return "stable"


# ══════════════════════════════════════════
# CLI
# ══════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    monitor = PositionMonitor()
    prefix = sys.argv[1] if len(sys.argv) > 1 else config.SEARCH_PREFIX
    
    # Determine source
    if config.GOOGLE_PLACES_API_KEY:
        source_name = "Google Places API"
    elif config.SERPAPI_KEY:
        source_name = "SerpApi"
    else:
        source_name = "Direct (free)"
    
    print(f"\n🔍 Checking autocomplete for prefix: '{prefix}'")
    print(f"🎯 Target: {config.BUSINESS_NAME}")
    print(f"📍 Location: {config.TARGET_CITY}")
    print(f"📡 Source: {source_name}\n")
    
    result = monitor.check_position(prefix)
    
    print(f"\n{'='*55}")
    if result['position'] > 0:
        print(f"  ✅ FOUND at position #{result['position']}")
        print(f"  📝 Text: \"{result['matched_text']}\"")
    else:
        print("  ❌ NOT FOUND in autocomplete")
    print(f"  📊 Total suggestions: {result['total']}")
    print(f"  📡 Source: {result['source']}")
    
    if result.get('suggestions'):
        print("\n  All suggestions:")
        for i, s in enumerate(result['suggestions'], 1):
            text = s if isinstance(s, str) else s.get("text", str(s))
            is_target = is_target_match(str(text))
            marker = " ← 🎯 TARGET" if is_target else ""
            print(f"    {i}. {text}{marker}")
    
    print(f"{'='*55}\n")
