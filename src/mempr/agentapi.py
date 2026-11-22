"""The memory over HTTP for agents: recall an entity, or run a read-only Cypher query.

Both read a throwaway copy of the vault. A query runs in a child process with a cap on its
memory and time, so a runaway match ends as an error and the server carries on.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import memory
from .readonly import is_read_only
from .vault import open_vault

MAX_ENTITY = 80  # characters
MAX_FACTS = 100
MAX_QUERY = 2000  # characters
MAX_ROWS = 200
MAX_RESULT = 256 * 1024  # bytes of JSON
TIME_LIMIT = 3.0  # seconds
MEMORY_LIMIT = 512 * 2**20  # bytes of address space for the process that runs a query
PARAM_TYPES = (str, int, float, bool, type(None))


class BadRequest(ValueError):
    """What was sent cannot be answered. `status` is the HTTP status to give."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def recall_entity(vault: Path, entity: Any) -> dict[str, Any]:
    """The current facts about one entity, as `mempr recall` gives them."""
    if not isinstance(entity, str) or not entity.strip():
        raise BadRequest("say which entity: /api/recall?entity=payments-api", 400)
    entity = entity.strip()
    if len(entity) > MAX_ENTITY or "\n" in entity or "\r" in entity:
        raise BadRequest(f"entity must be one line, {MAX_ENTITY} characters at most")
    facts = memory.recall(vault, entity)
    return {
        "entity": entity,
        "known": bool(facts),
        "facts": facts[:MAX_FACTS],
        "fact_count": len(facts),
        "truncated": len(facts) > MAX_FACTS,
    }


def check_query(body: Any) -> tuple[str, dict[str, Any]]:
    """The query and its parameters from a request body. Raises BadRequest."""
    if not isinstance(body, dict):
        raise BadRequest('send {"cypher": "MATCH ..."}')
    cypher, params = body.get("cypher"), body.get("params", {})
    if not isinstance(cypher, str) or not cypher.strip():
        raise BadRequest("cypher is required")
    if len(cypher) > MAX_QUERY:
        raise BadRequest(f"cypher may be {MAX_QUERY} characters at most")
    if not is_read_only(cypher):
        raise BadRequest(
            "this endpoint is read-only: CREATE, MERGE, SET, DELETE, REMOVE, DROP, FOREACH, "
            "INSERT, LOAD CSV and procedures that are not known to only read are refused. "
            "Change the memory with a pull request.",
            403,
        )
    if not isinstance(params, dict) or not all(
        isinstance(key, str) and isinstance(value, PARAM_TYPES) for key, value in params.items()
    ):
        raise BadRequest("params must be an object of text, numbers, true, false or null")
    return cypher, params


def _answer(vault: Path, cypher: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run in the child process: the first MAX_ROWS rows, or the engine's error.

    `vault` is the parent's throwaway copy, so it is opened as it is.
    """
    try:
        with open_vault(vault, in_place=True) as conn:
            cursor = conn.cursor()
            cursor.execute(cypher, params)
            found = [dict(row) for row in cursor.fetchmany(MAX_ROWS + 1)]
    except Exception as error:  # a query the engine refuses
        return {"error": str(error)[:500]}
    return {"rows": found[:MAX_ROWS], "truncated": len(found) > MAX_ROWS}


def run_query(
    vault: Path, body: Any, *, time_limit: float = TIME_LIMIT, memory_limit: int = MEMORY_LIMIT
) -> dict[str, Any]:
    """The rows of a read-only query, capped in rows, size, time and memory.

    The query runs in a child process on a copy of the vault that this process makes and
    removes, so the deployed files cannot change and nothing is left behind when the child
    is stopped.
    """
    cypher, params = check_query(body)
    with tempfile.TemporaryDirectory(prefix="mempr-query-") as tmp:
        copy = Path(tmp) / "vault"
        shutil.copytree(vault, copy, ignore=shutil.ignore_patterns(".relate", ".cypherlite.*"))
        request = {
            "vault": str(copy),
            "cypher": cypher,
            "params": params,
            "memory": memory_limit,
            "cpu": math.ceil(time_limit),
        }
        code, output = _run_child(json.dumps(request), time_limit)
    if code == "slow":
        raise BadRequest(f"the query took longer than {time_limit:g} seconds", 408)
    if code == "large":
        raise BadRequest(f"the result is larger than {MAX_RESULT // 1024} KB; return less", 413)
    if code != 0:
        # Killed for its CPU time, or it ran out of the memory it is allowed.
        raise BadRequest(
            "the query was stopped: it needed more than "
            f"{memory_limit // 2**20} MB of memory or {time_limit:g} seconds. "
            "Match less at once.",
            422,
        )
    answer = json.loads(output.decode("utf-8"))
    if "error" in answer:
        raise BadRequest(f"Relate refused the query: {answer['error']}", 400)
    return {
        "rows": answer["rows"],
        "row_count": len(answer["rows"]),
        "truncated": answer["truncated"],
    }


def _run_child(request: str, time_limit: float) -> tuple[int | str, bytes]:
    """Run the query process. Gives its exit code and output, or "slow" or "large".

    At most MAX_RESULT bytes of its output are ever read: past that it is killed, so a
    large result cannot fill this process's memory either.
    """
    child = subprocess.Popen(
        [sys.executable, "-m", "mempr.agentapi"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_child_env(),
    )
    output = bytearray()
    large = threading.Event()

    def read() -> None:
        while chunk := child.stdout.read1(64 * 1024):
            if len(output) + len(chunk) > MAX_RESULT:
                large.set()
                child.kill()
                return
            output.extend(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        child.stdin.write(request.encode("utf-8"))
        child.stdin.close()
        code: int | str = child.wait(timeout=time_limit)
    except subprocess.TimeoutExpired:
        code = "slow"
    except BrokenPipeError:
        code = 1
    finally:
        child.kill()
        child.wait()
        reader.join(timeout=5)
        child.stdout.close()
    return ("large" if large.is_set() else code), bytes(output)


def _child_env() -> dict[str, str]:
    """The parent's environment without secrets, and with this package importable."""
    import os

    keep = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG"}}
    here = str(Path(__file__).resolve().parent.parent)
    keep["PYTHONPATH"] = os.pathsep.join([here, *sys.path[1:]])
    return keep


if __name__ == "__main__":
    import resource

    asked = json.loads(sys.stdin.read())
    # A runaway match is stopped here, in the child, before it can take the server with it.
    resource.setrlimit(resource.RLIMIT_AS, (asked["memory"], asked["memory"]))
    resource.setrlimit(resource.RLIMIT_CPU, (asked["cpu"], asked["cpu"]))
    json.dump(
        _answer(Path(asked["vault"]), asked["cypher"], asked["params"]),
        sys.stdout,
        default=str,
        ensure_ascii=False,
    )
