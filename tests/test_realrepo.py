import json

import pytest

from mempr.realrepo import RepoError, entity_of, extract, learn, wanted
from mempr.rules import load_rules

OLD, NEW = "a" * 40, "b" * 40
FILES = {
    OLD: {
        "Dockerfile": "FROM golang:1.22 AS build\nRUN go build\nFROM alpine:3.19\n",
        "api/go.mod": "module x\n\ngo 1.22\n",
        "web/package.json": json.dumps({"engines": {"node": ">=18"}}),
    },
    NEW: {
        "Dockerfile": (
            "FROM golang:1.23 AS build\nFROM build\nFROM gcr.io/distroless/static:nonroot\n"
        ),
        "api/go.mod": "module x\n\ngo 1.22\n",
        "web/package.json": json.dumps(
            {"engines": {"node": ">=20"}, "packageManager": "pnpm@9.1.0+sha"}
        ),
        "node_modules/x/package.json": json.dumps({"engines": {"node": "0.1"}}),
    },
}


def fake(url):
    api = "https://api.github.com/repos/acme/shop"

    def commit(sha):
        return [
            {
                "sha": sha,
                "html_url": f"https://github.com/acme/shop/commit/{sha}",
                "commit": {"committer": {"date": "2025-01-01T00:00:00Z"}},
            }
        ]

    if url == api:
        return 200, json.dumps(
            {
                "full_name": "acme/shop",
                "default_branch": "main",
                "html_url": "https://github.com/acme/shop",
            }
        ).encode()
    if url.startswith(api + "/commits?"):
        return 200, json.dumps(commit(OLD if "until=" in url else NEW)).encode()
    if url.startswith(api + "/git/trees/"):
        sha = url.split("/trees/")[1].split("?")[0]
        return 200, json.dumps({"tree": [{"path": p, "type": "blob"} for p in FILES[sha]]}).encode()
    prefix = "https://raw.githubusercontent.com/acme/shop/"
    if url.startswith(prefix):
        sha, path = url[len(prefix) :].split("/", 1)
        return 200, FILES[sha][path].encode()
    return 404, b""


def test_extract_reads_only_what_a_file_states():
    assert extract("Dockerfile", FILES[NEW]["Dockerfile"]) == [
        ("base-image", "gcr.io/distroless/static:nonroot")
    ]
    assert extract("a/Dockerfile.dev", "FROM node\n") == [("base-image-dev", "node:latest")]
    assert extract("Dockerfile", "FROM localhost:5000/app@sha256:0123456789abcdef\n") == [
        ("base-image", "localhost:5000/app:latest@sha256:0123456789ab")
    ]
    assert extract(
        "pyproject.toml",
        '[tool.x]\nrequires-python = "no"\n[project]\nrequires-python = ">=3.11"\n',
    ) == [("python-requires", ">=3.11")]
    assert extract("Cargo.toml", '[package]\nedition = "2021"\nrust-version = "1.75"\n') == [
        ("rust-version", "1.75"),
        ("rust-edition", "2021"),
    ]
    assert extract(".nvmrc", "v20.11\n") == [("node-version", "v20.11")]
    assert extract("package.json", "not json") == []
    assert not wanted("node_modules/a/package.json") and not wanted("a/b/c/d/package.json")
    assert (
        entity_of("acme/shop", "Dockerfile") == "shop"
        and entity_of("acme/shop", "src/Web/go.mod") == "src-web"
    )


def test_learn_blocks_values_that_moved_and_passes_once_they_replace_the_old():
    out = learn("acme/shop", 12, load_rules(None), fetch=fake)
    moved = {(c["entity"], c["predicate"]): (c["old"], c["new"]) for c in out["changed"]}
    assert moved == {
        ("shop", "base-image"): ("alpine:3.19", "gcr.io/distroless/static:nonroot"),
        ("web", "node-engine"): (">=18", ">=20"),
    }
    assert out["learned"] == 3 and out["proposed"] == 3  # the go version did not move; pnpm is new
    assert out["blocked"] and out["rules"] == ["contradiction"] and len(out["problems"]) == 2
    assert not out["fixed"]["blocked"]
    ids = {n["id"] for n in out["after"]["nodes"]}
    assert "Fact/web--package-manager--pnpm-9-1-0" in ids and "Agent/builder" in ids
    source = next(n for n in out["before"]["nodes"] if n["id"].startswith("Source/dockerfile"))
    assert source["props"]["uri"] == f"https://github.com/acme/shop/blob/{OLD}/Dockerfile"


@pytest.mark.parametrize(
    "repo, months, status",
    [("not a repo", 12, 422), ("acme/shop", 0, 422), ("acme/missing", 12, 404)],
)
def test_learn_refuses_what_it_cannot_read(repo, months, status):
    with pytest.raises(RepoError) as error:
        learn(repo, months, load_rules(None), fetch=fake)
    assert error.value.status == status


def test_a_value_whose_slug_matches_the_old_one_is_still_a_change():
    files = {
        OLD: {"pyproject.toml": '[project]\nrequires-python = ">=3.10"\n'},
        NEW: {"pyproject.toml": '[project]\nrequires-python = "~=3.10"\n'},
    }

    def fetch(url):
        prefix = "https://raw.githubusercontent.com/acme/shop/"
        if url.startswith(prefix):
            sha, path = url[len(prefix) :].split("/", 1)
            return 200, files[sha][path].encode()
        if "/git/trees/" in url:
            sha = url.split("/trees/")[1].split("?")[0]
            return 200, json.dumps(
                {"tree": [{"path": p, "type": "blob"} for p in files[sha]]}
            ).encode()
        return fake(url)

    out = learn("acme/shop", 12, load_rules(None), fetch=fetch)
    assert [(c["old"], c["new"]) for c in out["changed"]] == [(">=3.10", "~=3.10")]
    assert out["blocked"] and not out["fixed"]["blocked"]


def test_a_file_github_does_not_send_fails_the_reading_instead_of_passing():
    def fetch(url):
        if url.endswith("/Dockerfile"):
            return 500, b""
        return fake(url)

    with pytest.raises(RepoError) as error:
        learn("acme/shop", 12, load_rules(None), fetch=fetch)
    assert error.value.status == 502 and "Dockerfile" in str(error.value)


def test_a_tree_github_cut_short_is_refused():
    def fetch(url):
        status, body = fake(url)
        if "/git/trees/" in url:
            return status, json.dumps({**json.loads(body), "truncated": True}).encode()
        return status, body

    with pytest.raises(RepoError) as error:
        learn("acme/shop", 12, load_rules(None), fetch=fetch)
    assert error.value.status == 422


def test_snapshots_without_supported_facts_are_not_reviewed_as_passing():
    def fetch(url):
        if "/git/trees/" in url:
            return 200, b'{"tree":[{"path":"README.md","type":"blob"}]}'
        return fake(url)

    with pytest.raises(RepoError, match="No supported facts found in either commit") as error:
        learn("acme/shop", 12, load_rules(None), fetch=fetch)
    assert error.value.status == 422


def test_long_values_that_differ_late_stay_distinct():
    long = "registry.example.com/" + "a" * 120
    first = extract("Dockerfile", f"FROM {long}:1\n")[0][1]
    second = extract("Dockerfile", f"FROM {long}:2\n")[0][1]
    assert len(first) == len(second) == 120 and first != second


def test_qualified_entities_name_the_repository():
    assert entity_of("o/shop", "frontend/Dockerfile") == "frontend"
    assert entity_of("o/shop", "frontend/Dockerfile", qualify=True) == "shop-frontend"
    assert entity_of("o/shop", "go.mod", qualify=True) == "shop"
