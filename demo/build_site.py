"""Generate the data the browser view (site/) shows: site/data/demo.json.

Replays the scripted demo agents in a throwaway git repository that starts from this
repository's memory/, one branch per scenario, and exports the graph and each review
with `mempr export`. Nothing here touches the checkout except the output file.

  python demo/build_site.py
"""

from __future__ import annotations

import argparse
import json
import os
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
    git(repo, "commit", "-q", "-m", subject, *(["-m", body] if body else []), author=author)


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
        commit(repo, "scout", "memory: what scout learned from the service files")

        agent.VAULT = vault  # the scenarios write to the throwaway repository
        specs = []
        for scenario, name in PULLS.items():
            branch = f"agent/{name}-{scenario}"
            git(repo, "checkout", "-q", "-b", branch, "main")
            summary = getattr(agent, f"scenario_{scenario.replace('-', '_')}")(name)
            commit(repo, name, f"memory: {summary}", agent.SCENARIOS[scenario])
            specs.append(branch)

        # The stale clone: while that agent worked, main moved. Another agent stamped the
        # same note on a different line, so git merges the two branches without a conflict.
        git(repo, "checkout", "-q", "-b", "main-moved", "main")
        note = vault / "Fact" / f"{fact_name('payments-api', 'database', 'postgres-15')}.md"
        head, _, body = note.read_text().rpartition("\n---\n")
        note.write_text(f"{head}\nverified: by-scout\n---\n{body}")
        commit(repo, "scout", "memory: Re-verified the payments-api database fact.")
        stale = f"agent/{PULLS['stale-clone']}-stale-clone"
        specs[specs.index(stale)] = f"main-moved..{stale}"

        return export(repo, "main", specs, "memory", "memory-rules.yaml")


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
