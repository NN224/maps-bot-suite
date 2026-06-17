"""Tests for the config-driven targeting in pr_progressive_prefix (SBO scenario).

Run: ./venv/bin/python tests/test_pr_targeting.py
(No pytest dependency — plain asserts so it runs on the bot's own venv.)

These guard the SAFETY-critical matcher: it must click OUR listing and never a
sibling listing that shares the brand (e.g. two locations of the same chain).
The fixtures use a fake example brand ("Neon Lounge") — real targeting lives in
the database, never in code.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scenarios import pr_progressive_prefix as m  # noqa: E402


def _cfg(**kw):
    ns = types.SimpleNamespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# Fake example brand. "Neon Lounge Downtown" is OUR listing; "Neon Lounge VIP"
# is a sibling that shares the brand and must be excluded.
BRAND = _cfg(
    SEARCH_PREFIXES=["neon", "neon l", "neon lounge", "neon lounge downtown"],
    MATCH_KEYWORDS=["neon lounge downtown", "downtown"],
    EXCLUDE_KEYWORDS=["vip", "competitor"],
    DEFAULT_PLACE_ID="ChIJ0000FAKEPLACEID000",
    BUSINESS_NAME="Neon Lounge Downtown",
)


def test_label_ok_clicks_our_listing():
    t = m._targeting(BRAND)
    assert m._label_ok("Neon Lounge Downtown", t) is True


def test_label_ok_rejects_sibling_listing():
    # VIP shares the brand, but exclude 'vip' must win.
    t = m._targeting(BRAND)
    assert m._label_ok("Neon Lounge VIP", t) is False


def test_label_ok_rejects_ambiguous_no_discriminator():
    t = m._targeting(BRAND)
    assert m._label_ok("Neon Lounge", t) is False


def test_label_ok_rejects_competitor():
    t = m._targeting(BRAND)
    assert m._label_ok("Competitor Bar", t) is False


def test_pattern_matches_only_our_listing():
    t = m._targeting(BRAND)
    assert t["pattern"].search("Neon Lounge Downtown")
    assert not t["pattern"].search("Neon Lounge VIP")


def test_config_drives_prefixes_and_place_id():
    t = m._targeting(BRAND)
    assert t["prefixes"][-1] == "neon lounge downtown"
    assert t["place_id"] == "ChIJ0000FAKEPLACEID000"


def test_fallback_is_neutral_without_config():
    # config=None must yield empty/neutral defaults (no built-in target).
    f = m._targeting(None)
    assert f["prefixes"] == []
    assert f["match"] == []
    # The neutral pattern must match nothing.
    assert not f["pattern"].search("Neon Lounge Downtown")


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
