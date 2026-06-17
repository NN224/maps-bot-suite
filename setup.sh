#!/usr/bin/env bash
# ==============================================================
# bot-suite — one-shot setup for a fresh machine.
# Safe to re-run (idempotent). Run from the repo root:
#     ./setup.sh
# ==============================================================
set -euo pipefail

# Always operate from the directory this script lives in (repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="venv"
PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

echo "==> bot-suite setup"
echo "    working dir: $SCRIPT_DIR"
echo

# --------------------------------------------------------------
# (a) Check Python 3
# --------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is not installed or not on PATH." >&2
  echo "       Install Python 3 (3.12+ recommended) and re-run." >&2
  exit 1
fi
echo "==> Found $(python3 --version)"

# --------------------------------------------------------------
# (b) Create venv if missing
# --------------------------------------------------------------
# Note: in this repo venv may be a symlink to a shared sibling project's venv.
# If it already resolves to a usable interpreter, we keep it.
if [ -x "$PY" ]; then
  echo "==> Reusing existing venv ($VENV_DIR)"
else
  echo "==> Creating virtualenv ($VENV_DIR)"
  python3 -m venv "$VENV_DIR"
fi

# --------------------------------------------------------------
# (c) Upgrade pip + install requirements
# --------------------------------------------------------------
echo "==> Upgrading pip"
"$PIP" install --upgrade pip

if [ -f requirements.txt ]; then
  echo "==> Installing Python dependencies (requirements.txt)"
  "$PIP" install -r requirements.txt
else
  echo "WARNING: requirements.txt not found — skipping dependency install." >&2
fi

# --------------------------------------------------------------
# (d) Install the browser (patchright preferred, playwright fallback)
# --------------------------------------------------------------
echo "==> Installing Chromium browser"
if "$PY" -m patchright install chromium; then
  echo "    patchright Chromium installed."
elif "$PY" -m playwright install chromium; then
  echo "    playwright Chromium installed (patchright unavailable)."
else
  echo "WARNING: browser install failed. Try manually:" >&2
  echo "         $PY -m patchright install chromium" >&2
fi

# --------------------------------------------------------------
# (e) Bootstrap .env from template
# --------------------------------------------------------------
ENV_CREATED=0
if [ -f .env ]; then
  echo "==> .env already exists — leaving it untouched"
elif [ -f .env.example ]; then
  cp .env.example .env
  ENV_CREATED=1
  echo "==> Created .env from .env.example"
else
  echo "WARNING: .env.example not found — cannot create .env." >&2
fi

# --------------------------------------------------------------
# (f) Next steps
# --------------------------------------------------------------
echo
echo "=============================================================="
echo " Setup complete."
echo "=============================================================="
if [ "$ENV_CREATED" -eq 1 ]; then
  echo " 1. EDIT .env and fill in your real values (DATABASE_URL, etc.)."
else
  echo " 1. Review .env and make sure your values are set."
fi
echo "    - With a Neon DATABASE_URL: Postgres is the source of truth."
echo "    - Leave DATABASE_URL empty to run local SQLite-only mode."
echo
echo " DATABASE: tables are created AUTOMATICALLY on first DB access."
echo "    - SQLite: the local mirror schema + a default sbo_config row"
echo "      are created the first time the bot connects (no manual step)."
echo "    - Neon Postgres: the schema must already exist in your Neon"
echo "      project (the bot does NOT create Postgres tables). If you are"
echo "      pointing at the shared companion-project Neon DB, it is already set"
echo "      up. For a brand-new empty Neon DB, create the tables there"
echo "      first, or run with DATABASE_URL empty to use SQLite."
echo
echo " 2. Add a target business:   ./bot biz add"
echo " 3. Make it active:          ./bot biz switch <slug>"
echo " 4. Test run (visible):      ./bot run 5 --visible"
echo
echo " Other useful commands: ./bot status | ./bot dash | ./bot web"
echo "=============================================================="
