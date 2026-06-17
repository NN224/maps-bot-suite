# 🤖 Bot Suite

**Local browser-automation suite for Google Maps** — drives organic search → click → engagement (and an optional autocomplete-burst mode) to support a Google Business Profile's local visibility. Runs locally, config-driven, one business at a time.

> 🇸🇦 دليل عربي مختصر: [README.ar.md](README.ar.md)

---

## ✨ What it does

Two run modes, one CLI:

| Mode | Command | What it does |
|------|---------|--------------|
| **Ranking + engagement** (default) | `./bot run 50` | Searches your keyword → scrolls results → clicks **your** listing → engages (reviews / photos / website / directions) |
| **Autocomplete burst** | `./bot run 50 --burst` | Types a short prefix → clicks your listing (reinforces the query→listing association) |

Your business data (name, place_id, keywords) lives in a **database**, never in the code — you manage it with `./bot biz`.

---

## 📋 Requirements

- **Python 3.10+** (3.12+ recommended)
- **git**
- A terminal. Works on **macOS**, **Linux (incl. Kali)**, and **Windows**.
- *(Optional)* a proxy and/or a Neon Postgres database — **not required to try it** (see Quick Start).

---

## 🚀 Quick Start

### 1. Get the code
```bash
git clone https://github.com/NN224/maps-bot-suite.git
cd maps-bot-suite
```

### 2. Install

**Debian / Kali (one-time prerequisites):** the venv module and browser system
libraries aren't always preinstalled:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

**macOS / Linux (incl. Kali):**
```bash
./setup.sh
```
That creates the virtualenv, installs dependencies, downloads the browser, and creates your `.env` from the template.

> **Kali/Linux:** if the browser later fails to launch with a missing-library error, install its system deps once:
> `venv/bin/python -m playwright install-deps chromium`  (or `sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libgbm1 libasound2`).

**Windows (PowerShell):** `setup.sh` is bash-only, so run these once:
```powershell
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
python -m patchright install chromium
copy .env.example .env
```
*(Or use WSL and run `./setup.sh`.)*

### 3. Try it immediately (no database, no proxy needed)
You can run it right away — leave `DATABASE_URL` **empty** in `.env` (it falls back to a local SQLite file that auto-creates), and leave the proxy off (`USE_PROXY=false`, it will use your real IP — fine for a quick visible test):

```bash
# macOS / Linux:
./bot biz add            # add your business (name, place_id, keywords)
./bot run 1 --visible    # watch one session in a real browser window

# Windows:
venv\Scripts\python bot biz add
venv\Scripts\python bot run 1 --visible
```

> On Windows run the CLI as `venv\Scripts\python bot <command>`. On macOS/Linux just use `./bot <command>`.

That's the whole loop. Everything below is detail.

---

## ⚙️ Configuration (`.env`)

All machine-level settings live in `.env` (copied from [`.env.example`](.env.example), which is fully commented). The important ones:

- **`DATABASE_URL`** — where data is stored.
  - **Leave EMPTY** → local **SQLite** file, tables auto-create. Easiest; great for a single machine.
  - Set a `postgresql://user:pass@host:5432/db` URL → use **Neon/Postgres** (multi-machine). Note: a brand-new Postgres DB needs its schema created first; SQLite does it automatically.
- **`USE_PROXY`** — `true`/`false`. For real campaigns set `true` and fill `PROXY_URL`.
- **`PROXY_URL`** — auto-detects these formats:
  ```
  host:port:user:pass            (DataImpulse style)
  user:pass@host:port
  http://user:pass@host:port
  socks5://user:pass@host:port
  ```
  Tip: if your provider supports sticky sessions, put `{session}` where the session id goes — each run gets a fresh IP.
- **`BLOCK_MEDIA`** — `true` blocks images to save proxy bandwidth (~10–16 MB/session).
- **`MOBILE_PCT` / `TABLET_PCT`** — `0/0` = desktop only (recommended).

> Business config (name, place_id, prefixes, geo) is **NOT** in `.env` — add it via `./bot biz add`.

---

## ▶️ Running

```bash
./bot run 50                 # 50 ranking sessions (background, headless)
./bot run 50 --burst         # 50 autocomplete-burst sessions
./bot run 5  --visible       # show the browser window
./bot run 100 --continuous   # run back-to-back until you stop it
./bot run 50 --free-proxies  # use free public proxies (low quality, $0)
./bot stop                   # stop a running batch
```
*(Windows: prefix with `venv\Scripts\python `, e.g. `venv\Scripts\python bot run 50`.)*

One bot runs at a time per project (a PID lock prevents overlap).

---

## 📊 Monitoring

```bash
./bot status     # today's stats (totals, ranks, engagement)
./bot dash       # live terminal dashboard (best) — Ctrl+C to exit
./bot log        # live log tail (every action, step by step, in English)
./bot web        # local web panel → http://127.0.0.1:8787
./bot app        # same panel as a native desktop window (needs: pip install pywebview)
```

---

## 🏢 Managing businesses

```bash
./bot biz                 # list
./bot biz add             # add a new target (asks name, place_id, keywords, type)
./bot biz switch <slug>   # set the active target
./bot biz edit <slug>     # edit fields
./bot biz delete <slug>   # remove (session history kept)
```

---

## 🔧 Troubleshooting

- **`./bot: permission denied` (macOS/Linux):** `chmod +x bot setup.sh`.
- **macOS “cannot be opened” / quarantine:** `xattr -d com.apple.quarantine bot`.
- **Windows: `./bot` doesn't run:** use `venv\Scripts\python bot <command>` (and activate the venv first with `venv\Scripts\activate`).
- **Browser won't launch / not found:** re-run `python -m patchright install chromium` (or `playwright install chromium`).
- **Kali/Linux: browser fails with a missing `.so` / library error:** install system deps once: `venv/bin/python -m playwright install-deps chromium` (needs sudo) or `sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libgbm1 libasound2`.
- **Windows: `./bot app` window is blank/empty:** install the Microsoft **WebView2 Runtime** (preinstalled on Windows 11; free download for Windows 10).
- **Kali headless (no display) for `--visible`:** `export DISPLAY=:99 && Xvfb :99 -screen 0 1024x768x24 &`.
- **“No businesses” error:** run `./bot biz add` first.
- **Dependency errors after an update:** versions are pinned in `requirements.txt`; reinstall with `pip install -r requirements.txt`.

---

## 📁 Project structure

```
bot-suite/
├── bot                 # CLI entry point
├── setup.sh            # one-shot installer (macOS/Linux)
├── .env.example        # config template (copy to .env)
├── requirements.txt    # pinned dependencies
├── shared/             # db, config, proxy, fingerprint, human behavior
├── scenarios/          # traffic_engage, pr_progressive_prefix, pr_burst, runner
├── ui/                 # terminal dashboard + local web panel
└── tests/              # unit tests
```

---

## ⚠️ Notes

- Start small (`./bot run 1 --visible`) and watch before large batches.
- Keep the machine awake during long continuous runs.
- Results build from consistency over time, not a single burst.
- Use responsibly and in line with the relevant platforms' terms.
