# CLAUDE.md — bot-suite project guide

> Read this first. 5-min orientation for any AI agent.

## 1. What this is

Local-only browser automation suite for Google Maps. Two scenario types:
- **sbo** → boost autocomplete ranking (progressive prefix typing → AC click)
- **traffic** → drive Maps profile visits (organic search → click → engage).
  NOT direct Place ID navigation — that signal is too weak (Google treats
  /maps/place/?q=place_id:X as bookmark/share, not discovery).

Forked from the companion website project to cleanly separate bot logic from
the public website. Shares the same Neon DB (single source of truth).

## 2. Folder map

```
bot-suite/
├── bot                  # ⭐ CLI entry (Typer + Rich) — main interface
├── shared/              # Reusable libs (db, config, fingerprint, proxy, ...)
│   ├── db.py            # Neon Postgres pool + SQLite mirror
│   ├── config.py        # Settings: Neon → env → defaults
│   ├── fingerprint.py   # browserforge + manual profiles
│   ├── human_behavior.py
│   ├── proxy_manager.py # DataImpulse / universal proxy
│   ├── position_monitor.py
│   └── utils.py
├── scenarios/           # Pluggable scenarios (the real engagement logic)
│   ├── runner.py        # Generic executor
│   ├── pr_progressive_prefix.py   # sbo (autocomplete progressive prefix)
│   ├── traffic_engage.py          # traffic (search → scroll → click → engage)
│   └── pr_burst.py                # autocomplete burst (./bot run --burst)
├── ui/
│   └── dashboard.py     # Rich Live Dashboard
├── apps/                # Empty — future: per-tool subcommands (ctr-bot etc)
├── logs/
├── venv → ../companion-project/venv   # symlinked to save space (same deps)
└── .env                 # Same as the companion project (same Neon DB)
```

## 3. Critical concepts

### Active business (single-target at a time)
- `businesses` table in Neon: one row per target
- `business_type`: `sbo` or `traffic`
- `is_active=true` on exactly one row → that's what the bot targets
- `set_active_business(slug)` flips the flag AND copies fields into `sbo_config`
  so `config.py` and the bot see the new target on next read

### Scenario selection (auto)
- `./bot run N` calls `scenarios.runner auto`
- The runner reads the active business's `business_type`
- Picks: `traffic_engage` if `traffic`, else `pr_progressive_prefix`
- You can pass an explicit scenario name to override

### Safety gates (CRITICAL — never relax)
1. `landed_on_pr=True` required before engagement
2. URL must contain "pr" or Place ID
3. Smart matcher requires a discriminator token
4. One bot at a time (`./bot run` refuses if another is running)

## 4. How to run

```bash
./bot                       # interactive (recommended)
./bot run 50                # 50 sessions bg+headless
./bot run 5 --visible       # 5 sessions, show browser
./bot status                # today's stats from Neon
./bot dash                  # Rich Live Dashboard (refreshes every 5s)
./bot web                   # local web control panel → http://127.0.0.1:8787
./bot log                   # tail live log
./bot stop                  # SIGTERM the runner

./bot biz                   # list businesses
./bot biz add               # create new (asks type, name, slug, place_id, ...)
./bot biz switch <id|slug>  # set active
./bot biz edit <id|slug>    # edit fields of an existing business
./bot biz delete <id|slug>  # remove a business (sessions kept)
```

Bot logs to `/tmp/bot_suite.log`. PID at `/tmp/bot_suite.pid`.

The web dashboard (`./bot web`, default port 8787, `--host`/`--port` to override)
uses only the stdlib `http.server` — no extra deps. Serves `ui/static/index.html`
and is local-only by default. See `ui/web.py`.

Debugging a single scenario directly (bypasses the `./bot run` one-bot lock):

```bash
python -m scenarios.runner auto --sessions 1 --headless    # or --no-proxy
python -m scenarios.runner pr_progressive_prefix --sessions 1
```

### Setup / dependencies
- Python **3.14** (see `__pycache__/*.cpython-314.pyc`).
- `venv` is symlinked to `../companion-project/venv` (same deps, saves space).
- Deps in `requirements.txt`: patchright/playwright, browserforge, psycopg2-binary,
  typer, rich. `python-ghost-cursor` is optional (graceful fallback if missing).

### Tests
There is **no automated test suite**. The untracked `test/` directory holds
ad-hoc scratch scripts (manual probes), not pytest tests — don't treat it as CI.

## 5. Known gotchas (same as the companion project)

1. **Patchright tracing breaks DNS** on macOS → never enable `tracing.start()`
2. **chrome-headless-shell** has no GUI → for `--visible` we force the full
   Chrome-for-Testing binary
3. **Mobile Maps web is much harder** than desktop → research showed
   desktop signals work fine for SBO; we default `MOBILE_PCT=0`
4. **`page.go_back()` after Website click** rewinds Maps to search results →
   we use popup capture instead (see `traffic_engage.py`)
5. **Greedy Escape** closes business panel too → prefer close buttons
6. **`Directions` on results list** = wrong business → matcher requires our
   business name in aria-label OR only one button visible
7. **Neon idle-closes after ~5min** → `_pg_exec` health-checks + reconnects
8. **Dashboard PIN** is in Neon (`sbo_config.dashboard_pin`), not env

## 6. Environment (.env)

Shared with the companion project so we're on the same Neon. Only 12 keys actually used:
- `DATABASE_URL` (Neon)
- `DASHBOARD_PIN`, `DASHBOARD_SECRET`, `OWNER_EMAIL`, `GMAIL_APP_PASSWORD`
- `GOOGLE_PLACES_API_KEY`
- `USE_PROXY`, `PROXY_PROVIDER`, `PROXY_URL` (DataImpulse)
- `BROWSER_CHANNEL` (empty for bundled Chromium)
- `MOBILE_PCT`, `TABLET_PCT` (0/0 = desktop only)

Business config (`BUSINESS_NAME`, `PLACE_ID`, prefixes, etc.) lives in the
Neon `businesses` table — NOT in env. Edit via `./bot biz`.

## 7. The 5 rules (DO NOT BREAK)

1. **Never use direct `goto(/maps/search/…)` URLs** in SBO scenarios — kills
   the typing signal
2. **`landed_on_pr=True` required** before any engagement
3. **Never log `.env` contents or DB passwords**
4. **One bot at a time** (the `./bot run` check enforces this)
5. **Same DB as the companion project** — don't fork it; just point both
   projects at the same Neon (already configured)

## 8. Common tasks

```bash
# Add a new business
./bot biz add

# Switch and run
./bot biz switch your-business-slug
./bot run 50

# Inspect Neon directly
PGPASSWORD='<your-db-password>' psql "$DATABASE_URL" \
  -c "SELECT id, slug, name, business_type, is_active FROM businesses;"

# Watch the live dashboard
./bot dash
```

## 9. Relationship to the companion project

Both projects read/write the **same** Neon. `bot-suite` runs the bot;
the companion website project provides the optional web dashboard (you can
use either).

Eventually the companion project will drop its `/sbo` routes and `bot/`
directory once `bot-suite` is fully validated.
