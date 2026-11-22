"""The small HTTP layer the functions in `api/` share: JSON in, JSON out, nothing logged."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any

MAX_BODY = 16 * 1024  # bytes
# The GitHub Pages mirror of the page calls this server from another origin. Nothing else may.
ALLOWED_ORIGINS = frozenset({"https://himanshu748.github.io"})


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def read_json(headers: Any, stream: Any) -> Any:
    """The request body as JSON. Raises HttpError for a missing, large or malformed body."""
    try:
        length = int(headers.get("Content-Length") or 0)
    except ValueError:
        raise HttpError(400, "Content-Length must be a number") from None
    if length <= 0:
        raise HttpError(400, "send a JSON body")
    if length > MAX_BODY:
        raise HttpError(413, f"the body may be {MAX_BODY} bytes at most")
    try:
        return json.loads(stream.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HttpError(400, "the body must be JSON") from None


def cors(origin: str | None) -> dict[str, str]:
    """Headers that let the allowed origins, and only them, read the response."""
    headers = {"Vary": "Origin"}
    if origin in ALLOWED_ORIGINS:
        headers |= {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
        }
    return headers


class Endpoint(BaseHTTPRequestHandler):
    """A JSON endpoint. A subclass defines `get(self, query)` and/or `post(self, body)`.

    Each returns a JSON-able value, or raises HttpError. Requests are not logged.
    """

    def _send(self, status: int, body: Any) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in cors(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _run(self, method: str, argument: Any) -> None:
        action = getattr(self, method, None)
        try:
            if action is None:
                raise HttpError(405, "method not allowed")
            self._send(200, action(argument() if callable(argument) else argument))
        except HttpError as error:
            self._send(error.status, {"error": error.message})
        except Exception:  # no details: they could echo what was sent
            self._send(500, {"error": "the server could not handle this request"})

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlsplit

        query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        self._run("get", query)

    def do_POST(self) -> None:  # noqa: N802
        self._run("post", lambda: read_json(self.headers, self.rfile))

    def _refuse(self) -> None:
        self._send(405, {"error": "method not allowed"})

    do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        for key, value in cors(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass
