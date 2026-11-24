#!/bin/sh
# memory-pr quickstart: serve the browser view and open the graph.
# Prerequisite: Python 3 (that is all — no install, no credentials).
# Usage: ./quickstart.sh [port]
set -eu

port="${1:-8619}"
dir="$(cd "$(dirname "$0")/site" && pwd)"
url="http://localhost:$port"

python3 -m http.server "$port" -d "$dir" >/dev/null 2>&1 &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT INT TERM
sleep 1

echo "Serving $url — the graph view opens in your browser. Ctrl+C to stop."
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$url" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$url" >/dev/null 2>&1 || true
else
  echo "No opener found — open $url yourself."
fi
wait $server
