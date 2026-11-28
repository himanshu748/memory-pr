"""Learn facts from a real public GitHub repository at two moments, and review the change.

An agent learned the repository at an earlier commit and its notes are the shared memory. A
second agent reads the code as it is today and proposes what it sees. The review is the same
`mempr check` the pull requests get: every value that moved since is a contradiction until
the new fact says which old one it replaces.

Facts come from files whose meaning is not in doubt: the final Dockerfile stage's base image, the
`engines` and `packageManager` of package.json, the `go` line of go.mod, `requires-python`,
Cargo's `rust-version` and `edition`, and .nvmrc / .python-version.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .check import review_vaults
from .export import graph_dict, problems
from .memory import fact_name, slug
from .report import MARKER, render
from .rules import Rule
from .vault import load, node_id, note_files

REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
MAX_FILES = 40
MAX_DEPTH = 4
MAX_BYTES = 16_000_000
SKIP = re.compile(r"(^|/)(node_modules|vendor|third_party|fixtures?|testdata|__tests__|\.[^/]+)/")
MANIFESTS = {"package.json", "go.mod", "pyproject.toml", "Cargo.toml", ".nvmrc", ".python-version"}
OLD_AGENT, NEW_AGENT = "scout", "builder"

Fetch = Callable[[str], tuple[int, bytes]]


class RepoError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _http(url: str, token: str | None = None) -> tuple[int, bytes]:
    headers = {"User-Agent": "memory-pr", "Accept": "application/vnd.github+json"}
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
            return r.status, r.read(MAX_BYTES)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, TimeoutError):
        return 0, b""


def wanted(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path.count("/") >= MAX_DEPTH or SKIP.search(path):
        return False
    return name in MANIFESTS or name == "Dockerfile" or name.startswith("Dockerfile.")


def _dockerfile_instructions(text: str) -> Iterator[str]:
    """Join Dockerfile continuation lines, preserving their whitespace."""
    escape = "\\"
    directives = True
    parts = []
    for line in text.removeprefix("\ufeff").splitlines():
        stripped = line.strip()
        if directives:
            directive = re.fullmatch(r"#\s*(syntax|escape|check)\s*=\s*(\S.*)", stripped, re.I)
            if directive:
                if directive[1].lower() == "escape" and directive[2] in ("\\", "`"):
                    escape = directive[2]
                continue
            directives = False
        if not stripped or stripped.startswith("#"):
            continue
        end = line.rstrip(" \t")
        if end.endswith(escape) and not end.endswith(escape * 2):
            parts.append(end[:-1])
        else:
            parts.append(line)
            yield "".join(parts)
            parts.clear()
    if parts:
        yield "".join(parts)


def _from_line(text: str) -> str | None:
    stages: dict[str, str | None] = {}
    last = None
    for line in _dockerfile_instructions(text):
        m = re.match(r"\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
        if not m:
            continue
        image = m.group(1)
        last = None if "$" in image else stages.get(image.lower(), image)
        if m.group(2):
            stages[m.group(2).lower()] = last
    if not last or last == "scratch":
        return last
    # The whole reference: a move to another registry or digest is a different base image.
    ref, _, digest = last.partition("@")
    if ":" not in ref.rsplit("/", 1)[-1]:
        ref += ":latest"
    return f"{ref}@{digest[:19]}" if digest else ref


def _toml(text: str, table: tuple[str, ...], key: str) -> str | None:
    section = None
    for line in text.splitlines():
        head = re.match(r"\s*\[([^\]]+)\]\s*(?:#.*)?$", line)
        if head:
            section = head.group(1).strip()
            continue
        m = re.match(rf'\s*{re.escape(key)}\s*=\s*["\']([^"\']+)["\']', line)
        if m and section in table:
            return m.group(1)
    return None


def extract(path: str, text: str) -> list[tuple[str, str]]:
    """(predicate, value) pairs one file states, nothing guessed."""
    name = path.rsplit("/", 1)[-1]
    out: list[tuple[str, str]] = []
    if name == "Dockerfile" or name.startswith("Dockerfile."):
        image = _from_line(text)
        if image:
            out.append(
                ("base-image" + ("" if name == "Dockerfile" else "-" + slug(name[11:])), image)
            )
    elif name == "package.json":
        try:
            pkg = json.loads(text)
        except ValueError:
            return []
        engines = pkg.get("engines") if isinstance(pkg, dict) else None
        if isinstance(engines, dict) and isinstance(engines.get("node"), str):
            out.append(("node-engine", engines["node"]))
        if isinstance(pkg, dict) and isinstance(pkg.get("packageManager"), str):
            out.append(("package-manager", pkg["packageManager"].split("+")[0]))
    elif name == "go.mod":
        # The module parser also accepts suffixes such as rc1 and beta1.
        m = re.search(
            r"^[ \t]*go[ \t]+([1-9][0-9]*\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?"
            r"(?:[a-z]+[0-9]+)?)[ \t]*(?://[^\r\n]*)?\r?$",
            text,
            re.M,
        )
        if m:
            out.append(("go-version", m.group(1)))
    elif name == "pyproject.toml":
        value = _toml(text, ("project",), "requires-python")
        if value:
            out.append(("python-requires", value))
    elif name == "Cargo.toml":
        for key in ("rust-version", "edition"):
            value = _toml(text, ("package", "workspace.package"), key)
            if value:
                out.append(("rust-" + key.removeprefix("rust-"), value))
    elif name == ".nvmrc":
        # nvm ignores blank lines, comments and reserved key/value metadata.
        lines = (line.partition("#")[0].strip() for line in text.splitlines())
        versions = [line for line in lines if line and "=" not in line]
        if len(versions) == 1 and len(versions[0]) < 40:
            out.append(("node-version", versions[0]))
    elif name == ".python-version":
        value = text.strip().splitlines()[0].strip() if text.strip() else ""
        if value and len(value) < 40:
            out.append(("python-version", value))
    return [(p, _short(v.strip())) for p, v in out if v.strip()]


def _short(value: str) -> str:
    """At most 120 characters; a longer value keeps a hash of the rest, so two stay distinct."""
    if len(value) <= 120:
        return value
    return f"{value[:111]}~{hashlib.sha1(value.encode()).hexdigest()[:8]}"


def entity_of(repo: str, path: str, qualify: bool = False) -> str:
    folder = path.rpartition("/")[0]
    name = repo.split("/", 1)[1]
    if qualify:
        return slug(f"{name}-{folder}" if folder else name)
    return slug(folder) or slug(name)


def _distinct_names(bases: dict[str, str]) -> dict[str, str]:
    """Keep unambiguous slugs and disambiguate distinct inputs with the same slug."""
    counts = Counter(bases.values())
    taken = set(counts)
    names = {}
    for original, base in sorted(bases.items()):
        if counts[base] == 1:
            names[original] = base
            continue
        suffix = hashlib.sha1(original.encode()).hexdigest()[:8]
        stem = name = f"{base}-{suffix}"
        n = 1
        while name in taken:
            n += 1
            name = f"{stem}-{n}"
        names[original] = name
        taken.add(name)
    return names


def _snapshot(
    repo: str, commit: dict[str, str], texts: dict[str, str], entities: dict[str, str]
) -> list[dict[str, str]]:
    facts = []
    for path in sorted(texts):
        for predicate, value in extract(path, texts[path]):
            facts.append(
                {
                    "entity": entities[path.rpartition("/")[0]],
                    "predicate": predicate,
                    "value": value,
                    "path": path,
                    "url": f"https://github.com/{repo}/blob/{commit['sha']}/{urllib.parse.quote(path)}",
                }
            )
    # One value per entity and predicate: the first file wins, so a fact is never doubled.
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for f in facts:
        seen.setdefault((f["entity"], f["predicate"]), f)
    return list(seen.values())


def _unconfirmed(
    then: list[dict[str, str]],
    now: list[dict[str, str]],
    head_paths: set[str],
    head_texts: dict[str, str],
) -> list[dict[str, str]]:
    """Explain why each older fact has no matching evidence in the latest scan."""
    checked = {(f["entity"], f["predicate"]) for f in now}
    missing = []
    for fact in then:
        if (fact["entity"], fact["predicate"]) in checked:
            continue
        path = fact["path"]
        if path not in head_paths:
            reason = "source-removed"
        elif path not in head_texts:
            reason = "source-unread"
        else:
            reason = "fact-not-found"
        missing.append(
            {
                "fact": fact["name"],
                "entity": fact["entity"],
                "predicate": fact["predicate"],
                "old": fact["value"],
                "old_url": fact["url"],
                "path": path,
                "reason": reason,
            }
        )
    return missing


def _note(
    folder: Path,
    label: str,
    name: str,
    props: dict[str, str],
    links: dict[str, list[str]],
    body: str = "",
) -> None:
    lines = ["---", f"type: {label}", f"name: {name}"]
    lines += [f"{k}: {json.dumps(v)}" for k, v in props.items()]
    for rel, targets in links.items():
        if targets:
            lines.append(f"{rel}:")
            lines += [f'  - "[[{t}]]"' for t in targets]
    path = folder / label / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines + ["---", "", body]) + "\n", encoding="utf-8")


def _write(
    vault: Path,
    facts: list[dict[str, str]],
    agent: str,
    sources: dict[str, str],
    replaces: dict[str, str],
) -> None:
    _note(vault, "Agent", agent, {}, {})
    for f in facts:
        source = sources[f["url"]]
        name = f["name"]
        _note(vault, "Entity", slug(f["entity"]), {}, {})
        _note(vault, "Source", source, {"uri": f["url"]}, {})
        _note(
            vault,
            "Fact",
            name,
            {"predicate": f["predicate"], "status": f.get("status", "active"), "value": f["value"]},
            {
                "ABOUT": [f"Entity/{slug(f['entity'])}"],
                "ASSERTED_BY": [f"Agent/{agent}"],
                "SOURCE": [f"Source/{source}"],
                "SUPERSEDES": [f"Fact/{replaces[name]}"] if name in replaces else [],
            },
            f"{f['entity']} {f['predicate']}: {f['value']}",
        )


def _graph(vault: Path) -> dict[str, Any]:
    notes = {
        node_id(vault, p): {
            "file": f"memory/{p.relative_to(vault).as_posix()}",
            "text": p.read_text(encoding="utf-8"),
        }
        for p in note_files(vault)
    }
    return graph_dict(load(vault), notes)


def _commit(c: dict[str, Any]) -> dict[str, str]:
    return {"sha": c["sha"], "date": c["commit"]["committer"]["date"], "url": c["html_url"]}


def learn(
    repo: str,
    months: int | None,
    rules: list[Rule],
    *,
    token: str | None = None,
    fetch: Fetch | None = None,
    qualify: bool = False,
) -> dict[str, Any]:
    """Review changes over `months`, or since the first parent of HEAD if None.

    `qualify` names entities after the repository too, so several repositories can share
    one memory without two `frontend` folders becoming one entity.
    """
    if not isinstance(repo, str) or not REPO.match(repo.strip()):
        raise RepoError(422, "repo must look like owner/name")
    repo = repo.strip()
    if months is not None and (not isinstance(months, int) or not 1 <= months <= 60):
        raise RepoError(422, "months must be a whole number from 1 to 60")
    get = fetch or (lambda url: _http(url, token))
    started = time.perf_counter()

    def api(path: str) -> Any:
        status, body = get("https://api.github.com" + path)
        if status == 404:
            raise RepoError(404, f"GitHub has no public repository {repo}")
        if status == 409:
            # An empty repository can still name a default branch; its commits list 409s.
            raise RepoError(422, f"{repo} has no commits yet; there is nothing to compare")
        if status in (403, 429):
            raise RepoError(503, "GitHub's rate limit was reached; try again in a few minutes")
        if status != 200:
            raise RepoError(502, "GitHub did not answer")
        try:
            return json.loads(body)
        except ValueError as error:
            raise RepoError(
                422, f"{repo} is too large to read here; try a smaller repository"
            ) from error

    meta = api(f"/repos/{repo}")
    if not isinstance(meta, dict) or (
        meta.get("private") is not False or meta.get("visibility", "public") != "public"
    ):
        raise RepoError(404, f"GitHub has no public repository {repo}")
    repo, branch = meta.get("full_name", repo), meta.get("default_branch")
    if not isinstance(branch, str) or not branch:
        # An empty repository has no default branch and no commits to compare.
        raise RepoError(422, f"{repo} has no commits yet; there is nothing to compare")
    commits = api(f"/repos/{repo}/commits?sha={urllib.parse.quote(branch)}&per_page=1")
    if not commits:
        raise RepoError(422, f"{repo} has no commits yet; there is nothing to compare")
    head = _commit(commits[0])
    if months is None:
        parents = commits[0].get("parents", [])
        if not parents:
            raise RepoError(422, f"{repo} has only one commit; there is no previous commit")
        old = _commit(api(f"/repos/{repo}/commits/{parents[0]['sha']}"))
    else:
        until = datetime.now(timezone.utc) - timedelta(days=round(months * 30.44))
        commits = api(
            f"/repos/{repo}/commits?sha={head['sha']}"
            f"&until={until.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=1"
        )
        if not commits:
            raise RepoError(
                422, f"{repo} has no commit that far back; choose fewer months or Previous commit"
            )
        old = _commit(commits[0])

    trees = {c["sha"]: api(f"/repos/{repo}/git/trees/{c['sha']}?recursive=1") for c in (old, head)}
    if any(t.get("truncated") for t in trees.values()):
        # GitHub listed only part of the files, so some would be missing from both readings.
        raise RepoError(422, f"{repo} is too large to read here; try a smaller repository")
    candidates = {
        sha: sorted(
            (i["path"] for i in t.get("tree", []) if i.get("type") == "blob" and wanted(i["path"])),
            key=lambda p: (p.count("/"), p),
        )
        for sha, t in trees.items()
    }
    paths = {sha: eligible[:MAX_FILES] for sha, eligible in candidates.items()}

    def raw(item: tuple[str, str]) -> tuple[str, str, str]:
        sha, path = item
        url = f"https://raw.githubusercontent.com/{repo}/{sha}/{urllib.parse.quote(path)}"
        status, body = get(url)
        if status != 200:
            status, body = get(url)
        if status != 200:
            # A file left out would hide what it states, and the review could pass on a gap.
            raise RepoError(502, f"GitHub did not send {path}; try again")
        return sha, path, body.decode("utf-8", "replace")

    texts: dict[str, dict[str, str]] = {old["sha"]: {}, head["sha"]: {}}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for sha, path, text in pool.map(raw, [(s, p) for s, ps in paths.items() for p in ps]):
            texts[sha][path] = text

    # Resolve folder names across both readings so a folder keeps its identity when
    # a different folder with the same slug appears or disappears.
    entities = _distinct_names(
        {
            path.rpartition("/")[0]: entity_of(repo, path, qualify)
            for snapshot in texts.values()
            for path in snapshot
        }
    )
    then = _snapshot(repo, old, texts[old["sha"]], entities)
    now = _snapshot(repo, head, texts[head["sha"]], entities)
    if not then and not now:
        raise RepoError(422, "No supported facts found in either commit. Try another repository.")
    sources = _distinct_names(
        {
            f["url"]: slug(f"{f['path']}@{sha[:7]}")
            for sha, facts in ((old["sha"], then), (head["sha"], now))
            for f in facts
        }
    )

    held = {fact_name(f["entity"], f["predicate"], f["value"]): f["value"] for f in then}
    for f in then:
        f["name"] = fact_name(f["entity"], f["predicate"], f["value"])
    for f in now:
        # `>=3.10` and `~=3.10` share a slug: the newer value gets a name of its own.
        base = fact_name(f["entity"], f["predicate"], f["value"])
        taken = base in held and held[base] != f["value"]
        f["name"] = f"{base}-{hashlib.sha1(f['value'].encode()).hexdigest()[:6]}" if taken else base
    known = {(f["entity"], f["predicate"]): f for f in then}
    proposed = [f for f in now if held.get(f["name"]) != f["value"]]
    changed = [
        {
            "entity": f["entity"],
            "predicate": f["predicate"],
            "old": known[k]["value"],
            "new": f["value"],
            "old_url": known[k]["url"],
            "new_url": f["url"],
            "replaces": (f["name"], known[k]["name"]),
        }
        for f in proposed
        if (k := (f["entity"], f["predicate"])) in known
    ]
    replaces = dict(c.pop("replaces") for c in changed)
    head_paths = {i["path"] for i in trees[head["sha"]].get("tree", []) if i.get("type") == "blob"}
    unconfirmed = _unconfirmed(then, now, head_paths, texts[head["sha"]])
    unconfirmed_names = {f["fact"] for f in unconfirmed}
    carried = [f | {"status": "unconfirmed"} if f["name"] in unconfirmed_names else f for f in then]

    with tempfile.TemporaryDirectory(prefix="mempr-learn-") as tmp:
        root = Path(tmp)
        before = root / "before"
        _write(before, then, OLD_AGENT, sources, {})
        before.mkdir(exist_ok=True)
        results = {}
        for kind, links in (("stale", {}), ("fixed", replaces)):
            after = root / kind
            _write(after, carried, OLD_AGENT, sources, {})
            _write(after, proposed, NEW_AGENT, sources, links)
            review = review_vaults(before, after, rules, base="memory", head=f"agent/{NEW_AGENT}")
            results[kind] = (review, after)
        stale, stale_vault = results["stale"]
        fixed, fixed_vault = results["fixed"]
        before_graph, after_graph, fixed_graph = (
            _graph(before),
            _graph(stale_vault),
            _graph(fixed_vault),
        )
        out = {
            "repo": repo,
            "url": meta["html_url"],
            "months": months,
            "old": old,
            "head": head,
            "files": {"old": len(texts[old["sha"]]), "head": len(texts[head["sha"]])},
            "files_skipped": {
                label: len(candidates[c["sha"]]) - len(paths[c["sha"]])
                for label, c in (("old", old), ("head", head))
            },
            "learned": len(then),
            "proposed": len(proposed),
            "changed": changed,
            "unconfirmed": unconfirmed,
            "before": before_graph,
            "after": after_graph,
            "fixed_after": fixed_graph,
            "blocked": stale.blocked,
            "rules": sorted({f.rule.id for f in stale.blocking}),
            "review": stale.to_dict(),
            "problems": problems(stale, after_graph),
            "comment": render(stale).replace(MARKER + "\n", "", 1),
            "fixed": {
                "blocked": fixed.blocked,
                "rules": sorted({f.rule.id for f in fixed.blocking}),
                "review": fixed.to_dict(),
                "problems": problems(fixed, fixed_graph),
                "comment": render(fixed).replace(MARKER + "\n", "", 1),
            },
        }
    out["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return out
