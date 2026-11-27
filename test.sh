#!/bin/sh
# Run every test from a fresh clone. No Docker, no API key, no npm install.
# Needs Python 3.10+ (with the venv module). Use Node.js 22+ for the JavaScript tests.
# Usage: ./test.sh
set -eu

cd "$(dirname "$0")"

# Probe the API the timeout tests use, so older versions with it still work.
if ! node -e 'require("node:test").mock.timers.enable({ apis: ["Date", "setTimeout"] })' >/dev/null 2>&1; then
    echo "error: the JavaScript tests need Node.js mock timers with Date support. Use Node.js 22 or newer." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  if ! python3 -m venv .venv; then
    echo "error: python3 -m venv failed. On Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  fi
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q -e ".[dev]"

echo "== JavaScript (no npm install) =="
node --test tests/js/*.test.mjs

echo "== Python (this also runs the JavaScript tests when node is on PATH) =="
pytest -q
