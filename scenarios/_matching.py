"""Shared targeting/matching helpers for scenarios.

Both the SBO (autocomplete) and traffic (search-ranking) scenarios must click
YOUR listing and never a sibling/competitor listing that shares the brand
(e.g. two locations of the same chain). They share this match/exclude logic so
the safety rule lives in exactly one place.
"""
import re


def build_pattern(keywords):
    """Compile match keywords into one regex (spaces → flexible whitespace)."""
    parts = [re.escape(k).replace(r"\ ", r"\s*").replace(" ", r"\s*")
             for k in keywords if k and k.strip()]
    return re.compile("|".join(parts), re.I) if parts else None


def label_ok(label, match, exclude):
    """Safety check on a candidate label: exclude wins, then require a match.

    Exclude is checked FIRST so a sibling listing whose name shares our brand
    tokens is rejected even though it contains them.
    """
    low = (label or "").lower()
    if any(x in low for x in exclude):
        return False
    if match and not any(m in low for m in match):
        return False
    return True


def resolve_match(config, *, default_match=None, default_exclude=None, default_pattern=None):
    """Read match/exclude/place_id targeting from the active business config.

    Returns a dict: pattern (regex to LOCATE candidates), match + exclude
    (lowercased keyword lists for label verification), place_id, name.
    Scenarios add their own prefixes/queries on top.
    """
    match = [k.lower() for k in (getattr(config, "MATCH_KEYWORDS", None) or [])] or list(default_match or [])
    exclude = [k.lower() for k in (getattr(config, "EXCLUDE_KEYWORDS", None) or [])] or list(default_exclude or [])
    pattern = build_pattern(match) or default_pattern
    return {
        "pattern": pattern,
        "match": match,
        "exclude": exclude,
        "place_id": (getattr(config, "DEFAULT_PLACE_ID", "") or ""),
        "name": (getattr(config, "BUSINESS_NAME", "") or "target business"),
    }
