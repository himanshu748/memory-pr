"""Thin wrappers around the git plumbing the check needs."""

from __future__ import annotations

import io
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def rev_parse(repo: Path, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").stdout.strip()


def merge_base(repo: Path, a: str, b: str) -> str:
    proc = git(repo, "merge-base", a, b, check=False)
    if proc.returncode != 0:
        raise GitError(f"no common ancestor between {a!r} and {b!r}")
    return proc.stdout.strip()


@dataclass(frozen=True)
class MergeResult:
    tree: str | None
    conflicts: tuple[str, ...]


def merge_tree(repo: Path, base: str, head: str) -> MergeResult:
    """Merge `head` into `base` the way git would, without touching the work tree."""
    proc = git(repo, "merge-tree", "--write-tree", "--name-only", base, head, check=False)
    lines = proc.stdout.splitlines()
    if proc.returncode == 0:
        return MergeResult(tree=lines[0], conflicts=())
    if proc.returncode == 1:
        conflicts = []
        for line in lines[1:]:
            if not line:
                break
            conflicts.append(line)
        return MergeResult(tree=None, conflicts=tuple(conflicts))
    raise GitError(
        f"git merge-tree --write-tree failed (it needs git 2.38+): {proc.stderr.strip()}"
    )


def export_tree(repo: Path, treeish: str, subdir: str, dest: Path) -> Path:
    """Write `subdir` as it is at `treeish` into `dest`; returns the exported vault path."""
    out = dest / subdir
    listing = git(repo, "ls-tree", "-d", "--name-only", treeish, subdir).stdout.strip()
    if not listing:
        out.mkdir(parents=True, exist_ok=True)
        return out
    proc = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", treeish, subdir],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git archive {treeish} failed: {proc.stderr.decode().strip()}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return out


def read_file(repo: Path, rev: str, path: str) -> str | None:
    """The file's text at `rev`, or None when it does not exist there."""
    proc = git(repo, "show", f"{rev}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None
