"""mempr command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, export, gitutil, memory
from .check import review
from .report import render
from .rules import load_rules
from .vault import open_vault, rows


def _repo(args: argparse.Namespace) -> Path:
    return Path(args.repo).resolve()


def _vault(args: argparse.Namespace) -> Path:
    return _repo(args) / args.vault


def cmd_check(args: argparse.Namespace) -> int:
    # Rules come from the base branch, so a pull request cannot switch off its own review.
    rules = load_rules(gitutil.read_file(_repo(args), args.base, args.rules))
    try:
        result = review(_repo(args), args.base, args.head, args.vault, rules)
    except gitutil.GitError as err:
        sys.stderr.write(f"mempr check: {err}\n")
        return 2
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=2) + "\n", "utf-8")
    text = render(result)
    if args.out:
        Path(args.out).write_text(text, "utf-8")
    else:
        sys.stdout.write(text)
    return 1 if result.blocked else 0


def cmd_query(args: argparse.Namespace) -> int:
    with open_vault(_vault(args)) as conn:
        for row in rows(conn, args.cypher):
            print(json.dumps(row, default=str, ensure_ascii=False))
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    facts = memory.recall(_vault(args), args.entity)
    if args.as_json:
        print(json.dumps(facts, indent=2, ensure_ascii=False))
        return 0
    if not facts:
        print(f"No facts about {args.entity}. Do not guess: check the source and add one.")
        return 0
    for f in facts:
        sources = ", ".join(f["sources"]) or "no source"
        agents = ", ".join(f["agents"]) or "unknown"
        print(f"{args.entity} {f['predicate']} = {f['value']}  [{sources}; by {agents}]")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    name = memory.add_fact(
        _vault(args),
        entity=args.entity,
        predicate=args.predicate,
        value=args.value,
        agent=args.agent,
        source=args.source,
        statement=args.statement,
        supersedes=args.supersedes,
        derived_from=args.derived_from,
    )
    print(f"Fact/{name}")
    return 0


def cmd_retract(args: argparse.Namespace) -> int:
    memory.retract(_vault(args), args.fact)
    print(f"retracted Fact/{args.fact}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import Settings, serve

    serve(Settings(_repo(args), args.vault, args.base, args.rules, args.remote))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mempr", description="Pull requests for agent memory, checked with Relate."
    )
    parser.add_argument("--version", action="version", version=f"mempr {__version__}")
    parser.add_argument("--repo", default=".", help="git repository (default: current directory)")
    parser.add_argument("--vault", default="memory", help="vault folder inside the repository")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="review merging one revision into another")
    check.add_argument("--base", default="origin/main", help="branch the change merges into")
    check.add_argument("--head", default="HEAD", help="revision with the change")
    check.add_argument(
        "--rules", default="memory-rules.yaml", help="extra rules file, read from the base branch"
    )
    check.add_argument("--out", help="write the Markdown comment to this file")
    check.add_argument("--json", help="write the review as JSON to this file")
    check.set_defaults(func=cmd_check)

    query = sub.add_parser("query", help="run a Cypher query on the vault")
    query.add_argument("cypher")
    query.set_defaults(func=cmd_query)

    recall = sub.add_parser("recall", help="current facts about an entity, with sources")
    recall.add_argument("entity")
    recall.add_argument("--json", dest="as_json", action="store_true")
    recall.set_defaults(func=cmd_recall)

    add = sub.add_parser("add-fact", help="write a fact into the working tree")
    add.add_argument("--entity", required=True)
    add.add_argument("--predicate", required=True)
    add.add_argument("--value", required=True)
    add.add_argument("--agent", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--statement", default="")
    add.add_argument("--supersedes", action="append", metavar="FACT")
    add.add_argument("--derived-from", action="append", metavar="FACT")
    add.set_defaults(func=cmd_add)

    retract = sub.add_parser("retract", help="mark a fact as retracted")
    retract.add_argument("fact")
    retract.set_defaults(func=cmd_retract)
    export.add_parser(sub)

    mcp = sub.add_parser("mcp", help="serve the memory to an agent over MCP (stdio)")
    mcp.add_argument("--base", default="origin/main", help="branch proposals start from")
    mcp.add_argument("--rules", default="memory-rules.yaml", help="extra rules file on the base")
    mcp.add_argument("--remote", default="origin", help="remote that pull requests are pushed to")
    mcp.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
