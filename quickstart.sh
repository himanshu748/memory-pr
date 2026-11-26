#!/bin/sh
# memory-pr quickstart: serve the browser view and open the graph.
# Prerequisite: Python 3 (that is all — no install, no credentials).
# Usage: ./quickstart.sh [port]
set -eu

exec python3 - "$0" "${1:-8619}" <<'PY'
import errno
import functools
import http.server
import pathlib
import sys
import threading
import webbrowser

try:
    port = int(sys.argv[2])
    if not 1 <= port <= 65535:
        raise ValueError
except ValueError:
    sys.exit("error: choose a port between 1 and 65535.")

directory = pathlib.Path(sys.argv[1]).resolve().parent / "site"
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
try:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
except OSError as error:
    if error.errno == errno.EADDRINUSE:
        print(f"error: port {port} is already serving something else.", file=sys.stderr)
    else:
        print(f"error: the server could not start on port {port}: {error}", file=sys.stderr)
    alternate_port = port + 1 if port < 65535 else 8620
    sys.exit(f"retry with a free port, e.g.: ./quickstart.sh {alternate_port}")

url = f"http://127.0.0.1:{port}"
print(f"Serving {url}. Ctrl+C to stop.", flush=True)

def open_graph():
    if not webbrowser.open(url):
        print(f"Open {url} in your browser.", flush=True)

threading.Thread(target=open_graph, daemon=True).start()
with server:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
PY
