#!/bin/sh
# memory-pr quickstart: serve the browser view and open the graph.
# Prerequisite: Python 3 (that is all — no install, no credentials).
# Usage: ./quickstart.sh [port]
set -eu

port="${1:-8619}"
dir="$(cd "$(dirname "$0")/site" && pwd)"
url="http://localhost:$port"

# If something already answers on the port, refuse before opening a browser
# at a service that is not this project.
if curl -sf -o /dev/null "$url/" 2>/dev/null; then
  echo "error: port $port is already serving something else." >&2
  echo "retry with a free port, e.g.: ./quickstart.sh $((port + 1))" >&2
  exit 1
fi

python3 -m http.server "$port" -d "$dir" >/dev/null 2>&1 &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT INT TERM

# Wait until the server actually answers. If startup fails (bind error etc.)
# the python process exits — report that instead of claiming success.
i=0
while [ "$i" -lt 40 ]; do
  if ! kill -0 "$server" 2>/dev/null; then
    echo "error: the server could not start on port $port." >&2
    echo "retry with a free port, e.g.: ./quickstart.sh $((port + 1))" >&2
    exit 1
  fi
  if curl -sf -o /dev/null "$url/" 2>/dev/null; then
    break
  fi
  i=$((i + 1))
  sleep 0.25
done
if [ "$i" -ge 40 ]; then
  echo "error: no response from $url after 10 seconds." >&2
  exit 1
fi

echo "Serving $url — the graph view opens in your browser. Ctrl+C to stop."
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$url" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$url" >/dev/null 2>&1 || true
else
  echo "No opener found — open $url yourself."
fi
wait $server
