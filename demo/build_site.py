"""Generate the data the browser view (site/) shows: site/data/demo.json.

Replays the scripted demo agents in a throwaway git repository that starts from this
repository's memory/, one branch per scenario, and exports the graph and each review
with `mempr export`. Nothing here touches the checkout except the output file.

  python demo/build_site.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo"))

import agent  # noqa: E402

from mempr.export import export  # noqa: E402
from mempr.memory import fact_name  # noqa: E402
from mempr.vault import note_files  # noqa: E402

# Scenario -> the agent that opens the pull request.
PULLS = {
    "contradiction": "builder",
    "hop": "reasoner",
    "supersede": "planner",
    "stale-clone": "auditor",
    "unsourced": "intern",
    "clean": "mapper",
}
# Fixed identity and date, so the same memory always gives the same file.
DATE = "2026-01-01T00:00:00+00:00"


def git(repo: Path, *args: str, author: str = "scout") -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": f"{author}-agent",
        "GIT_AUTHOR_EMAIL": f"{author}@agents.invalid",
        "GIT_COMMITTER_NAME": f"{author}-agent",
        "GIT_COMMITTER_EMAIL": f"{author}@agents.invalid",
        "GIT_AUTHOR_DATE": DATE,
        "GIT_COMMITTER_DATE": DATE,
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def commit(repo: Path, author: str, subject: str, body: str = "") -> None:
    git(repo, "add", "-A")
    # --no-gpg-sign: a machine with commit.gpgsign would otherwise change every hash.
    git(
        repo,
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        subject,
        *(["-m", body] if body else []),
        author=author,
    )


def memory_digest(source: Path = ROOT) -> str:
    """Identifies the memory the page data was built from."""
    digest = hashlib.sha256()
    for note in sorted(note_files(source / "memory")):
        digest.update(note.relative_to(source / "memory").as_posix().encode())
        digest.update(note.read_bytes())
    return digest.hexdigest()[:16]


def build(source: Path = ROOT) -> dict:
    with tempfile.TemporaryDirectory(prefix="mempr-site-") as tmp:
        repo = Path(tmp) / "repo"
        vault = repo / "memory"
        for note in note_files(source / "memory"):
            target = vault / note.relative_to(source / "memory")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(note, target)
        shutil.copyfile(source / ".gitignore", repo / ".gitignore")
        git(repo, "init", "-q", "-b", "main")
        # As in this repository's history: scout wrote the memory, then the database fact
        # was re-verified on main. The stale clone starts from before that stamp.
        note = vault / "Fact" / f"{fact_name('payments-api', 'database', 'postgres-15')}.md"
        current = note.read_text()
        note.write_text(re.sub(r"^verified: .*\n", "", current, flags=re.M))
        commit(repo, "scout", "memory: what scout learned from the service files")
        git(repo, "tag", "stale-base")
        if note.read_text() != current:
            note.write_text(current)
            commit(repo, "scout", "memory: Re-verified the payments-api database fact.")

        agent.VAULT = vault  # the scenarios write to the throwaway repository
        specs = []
        for scenario, name in PULLS.items():
            branch = f"agent/{name}-{scenario}"
            start = "stale-base" if scenario == "stale-clone" else "main"
            git(repo, "checkout", "-q", "-b", branch, start)
            summary = getattr(agent, f"scenario_{scenario.replace('-', '_')}")(name)
            commit(repo, name, f"memory: {summary}", agent.SCENARIOS[scenario])
            specs.append(branch)

        data = export(repo, "main", specs, "memory", "memory-rules.yaml")
        return {**data, "memory_digest": memory_digest(source)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(ROOT / "site" / "data" / "demo.json"))
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(), indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
