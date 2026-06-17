"""Tests for the traffic/ranking listing-selection logic.

Run: ./venv/bin/python tests/test_traffic_ranking.py

The scroll-to-find loop in traffic_engage scans result labels in feed order and
picks the FIRST one that passes the shared match/exclude check. These tests
cover that selection (the browser scrolling itself needs a live page), with the
critical case: a sibling listing ranking ABOVE us must be skipped. Fixtures use
a fake example brand ("Neon Lounge") — real targeting lives in the database.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scenarios._matching import label_ok, resolve_match  # noqa: E402


def _cfg(**kw):
    ns = types.SimpleNamespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


BRAND = _cfg(
    MATCH_KEYWORDS=["neon lounge downtown", "downtown"],
    EXCLUDE_KEYWORDS=["vip"],
    DEFAULT_PLACE_ID="ChIJ0000FAKEPLACEID000",
    BUSINESS_NAME="Neon Lounge Downtown",
)


def _first_match_position(labels, t):
    """Mirror the selection inside _find_listing_by_scroll (1-based)."""
    for idx, lab in enumerate(labels):
        if label_ok(lab, t["match"], t["exclude"]):
            return idx + 1, lab
    return None, None


def test_picks_us_and_skips_sibling_above_it():
    t = resolve_match(BRAND)
    results = [
        "Competitor Bar",
        "Neon Lounge VIP",          # sibling — must be skipped
        "Some Other Lounge",
        "Neon Lounge Downtown",     # us — position 4
    ]
    pos, lab = _first_match_position(results, t)
    assert pos == 4, f"expected position 4, got {pos}"
    assert "downtown" in lab.lower()


def test_returns_none_when_only_sibling_present():
    t = resolve_match(BRAND)
    results = ["Neon Lounge VIP", "Competitor Bar"]
    pos, lab = _first_match_position(results, t)
    assert pos is None and lab is None


def test_picks_us_at_top_when_we_rank_first():
    t = resolve_match(BRAND)
    results = ["Neon Lounge Downtown", "Neon Lounge VIP"]
    pos, lab = _first_match_position(results, t)
    assert pos == 1


def test_traffic_query_comes_from_search_prefixes():
    cfg = _cfg(SEARCH_PREFIXES=["neon lounge"], BUSINESS_NAME="Neon Lounge Downtown | x")
    queries = list(getattr(cfg, "SEARCH_PREFIXES", None) or [])
    biz_name = cfg.BUSINESS_NAME.split("|")[0].strip()
    query = (queries[0] if queries else biz_name).strip()
    assert query == "neon lounge"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print(f"  ✅ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {fn.__name__}  {e}")
    print(f"\n{'=' * 40}\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
