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


def fake(url, *, files=FILES, parents=(OLD,)):
    api = "https://api.github.com/repos/acme/shop"

    def commit(sha):
        return {
            "sha": sha,
            "html_url": f"https://github.com/acme/shop/commit/{sha}",
            "commit": {"committer": {"date": "2025-01-01T00:00:00Z"}},
            "parents": [{"sha": parent} for parent in parents] if sha == NEW else [],
        }

    if url == api:
        return 200, json.dumps(
            {
                "full_name": "acme/shop",
                "default_branch": "main",
                "html_url": "https://github.com/acme/shop",
                "private": False,
                "visibility": "public",
            }
        ).encode()
    if url.startswith(api + "/commits?"):
        if "until=" in url:
            assert f"sha={NEW}&until=" in url
        return 200, json.dumps([commit(OLD if "until=" in url else NEW)]).encode()
    if url == f"{api}/commits/{OLD}":
        return 200, json.dumps(commit(OLD)).encode()
    if url.startswith(api + "/git/trees/"):
        sha = url.split("/trees/")[1].split("?")[0]
        return 200, json.dumps({"tree": [{"path": p, "type": "blob"} for p in files[sha]]}).encode()
    prefix = "https://raw.githubusercontent.com/acme/shop/"
    if url.startswith(prefix):
        sha, path = url[len(prefix) :].split("/", 1)
        return 200, files[sha][path].encode()
    return 404, b""


def test_an_empty_repository_is_a_clear_error_not_a_500():
    def fake(url):
        if url == "https://api.github.com/repos/acme/empty":
            return 200, json.dumps(
                {
                    "full_name": "acme/empty",
                    "default_branch": None,
                    "html_url": "x",
                    "private": False,
                }
            ).encode()
        return 404, b""

    with pytest.raises(RepoError) as error:
        learn("acme/empty", 6, load_rules(None), fetch=fake)
    assert error.value.status == 422 and "no commits" in str(error.value)


def test_an_empty_repository_can_still_name_a_default_branch():
    def fake(url):
        if url == "https://api.github.com/repos/acme/empty":
            return 200, json.dumps(
                {
                    "full_name": "acme/empty",
                    "default_branch": "master",
                    "html_url": "x",
                    "private": False,
                }
            ).encode()
        if "/commits?" in url:
            return 409, b'{"message": "Git Repository is empty."}'
        return 404, b""

    with pytest.raises(RepoError) as error:
        learn("acme/empty", 6, load_rules(None), fetch=fake)
    assert error.value.status == 422 and "no commits" in str(error.value)


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
    assert out["files_skipped"] == {"old": 0, "head": 0}
    assert out["unconfirmed"] == []
    assert out["blocked"] and out["rules"] == ["contradiction"] and len(out["problems"]) == 2
    assert not out["fixed"]["blocked"]
    ids = {n["id"] for n in out["after"]["nodes"]}
    assert "Fact/web--package-manager--pnpm-9-1-0" in ids and "Agent/builder" in ids
    source = next(n for n in out["before"]["nodes"] if n["id"].startswith("Source/dockerfile"))
    assert source["props"]["uri"] == f"https://github.com/acme/shop/blob/{OLD}/Dockerfile"


def test_review_reads_commented_runtime_versions():
    def manifests(go, python):
        return {
            "api/go.mod": f"module acme/shop\r\n\r\n\tgo {go} // Minimum for CI\r\n",
            "web/pyproject.toml": f'[project] # Package metadata\nrequires-python = "{python}"\n',
        }

    files = {OLD: manifests("1.22", ">=3.10"), NEW: manifests("1.23.0", ">=3.11")}
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert {c["predicate"]: (c["old"], c["new"]) for c in out["changed"]} == {
        "go-version": ("1.22", "1.23.0"),
        "python-requires": (">=3.10", ">=3.11"),
    }
    assert out["blocked"] and not out["fixed"]["blocked"]


def test_review_reads_a_go_release_candidate_before_a_stable_release():
    files = {
        OLD: {"go.mod": "module acme/shop\n\ngo 1.23rc1 // preview\n"},
        NEW: {"go.mod": "module acme/shop\n\ngo 1.23.0\n"},
    }
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert [(change["predicate"], change["old"], change["new"]) for change in out["changed"]] == [
        ("go-version", "1.23rc1", "1.23.0")
    ]
    assert out["blocked"] and not out["fixed"]["blocked"]


def test_commented_toml_header_ends_the_previous_table():
    content = (
        '[project]\nname = "shop"\n[tool.example] # Tool-only setting\nrequires-python = ">=3.12"\n'
    )
    assert extract("pyproject.toml", content) == []


def test_toml_example_inside_a_multiline_string_is_not_manifest_evidence():
    doc = '[tool.examples]\ntext = """\n[project]\nrequires-python = ">=9.0"\n"""\n'
    assert extract("pyproject.toml", doc) == []


def test_review_uses_structured_toml_values_instead_of_documentation_examples():
    def manifest(version):
        return (
            '[tool.examples]\ntext = """\n[project]\nrequires-python = ">=9.0"\n"""\n'
            f'["project"]\n"requires-python" = ">={version}"\n'
        )

    files = {
        OLD: {"pyproject.toml": manifest("3.11")},
        NEW: {"pyproject.toml": manifest("3.12")},
    }
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert [(change["predicate"], change["old"], change["new"]) for change in out["changed"]] == [
        ("python-requires", ">=3.11", ">=3.12")
    ]
    assert out["blocked"] and not out["fixed"]["blocked"]


def test_cargo_reads_local_and_inherited_settings_from_their_tables():
    doc = (
        '[workspace.package]\nrust-version = "1.75"\nedition = "2021"\n'
        '[package]\nrust-version.workspace = true\nedition = "2024"\n'
    )
    assert extract("Cargo.toml", doc) == [("rust-version", "1.75"), ("rust-edition", "2024")]


def test_cargo_workspace_settings_do_not_apply_to_an_opted_out_root_package():
    workspace = '[workspace.package]\nedition = "2021"\n'
    assert extract("Cargo.toml", workspace) == [("rust-edition", "2021")]
    assert extract("Cargo.toml", f'{workspace}[package]\nname = "shop"\n') == []


@pytest.mark.parametrize("latest", ["v20.11", "v22.13"])
def test_review_reads_nvmrc_version_instead_of_comments(latest):
    files = {
        OLD: {"web/.nvmrc": "# Old explanation\nmetadata=stable\n\tv20.11 # runtime\n"},
        NEW: {"web/.nvmrc": f"# New explanation\nmetadata=lts\n\n  {latest} # runtime\n"},
    }
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    expected = (
        []
        if latest == "v20.11"
        else [
            {
                "entity": "web",
                "predicate": "node-version",
                "old": "v20.11",
                "new": latest,
                "old_url": f"https://github.com/acme/shop/blob/{OLD}/web/.nvmrc",
                "new_url": f"https://github.com/acme/shop/blob/{NEW}/web/.nvmrc",
            }
        ]
    )
    assert out["changed"] == expected
    assert out["blocked"] is bool(expected)
    assert not out["fixed"]["blocked"]


@pytest.mark.parametrize("content", ["# Explanation only\n", "metadata=lts\n", "v20\nv22\n"])
def test_nvmrc_does_not_guess_a_missing_or_ambiguous_version(content):
    assert extract(".nvmrc", content) == []


def test_review_reads_the_literal_base_through_named_docker_stages():
    dockerfile = "FROM alpine:{} AS base\nFROM base AS app\nFROM app AS final\n"
    files = {
        OLD: {"Dockerfile": dockerfile.format("3.19")},
        NEW: {"Dockerfile": dockerfile.format("3.20")},
    }
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert out["changed"] == [
        {
            "entity": "shop",
            "predicate": "base-image",
            "old": "alpine:3.19",
            "new": "alpine:3.20",
            "old_url": f"https://github.com/acme/shop/blob/{OLD}/Dockerfile",
            "new_url": f"https://github.com/acme/shop/blob/{NEW}/Dockerfile",
        }
    ]
    assert out["blocked"] and not out["fixed"]["blocked"]


@pytest.mark.parametrize("escape", ["\\", "`"])
def test_review_reads_multiline_docker_stage_bases(escape):
    directive = f"# syntax=docker/dockerfile:1\n# escape={escape}\n" if escape == "`" else ""

    def dockerfile(version):
        return (
            f"{directive}FROM --platform=$BUILDPLATFORM {escape}\n"
            f"    # Pinned version for the final stage\n    alpine:{version} AS {escape}\n"
            f"    base\nFROM {escape}\n    base AS final\n"
            f"RUN echo {escape}\n    FROM not-an-instruction:1\n"
        )

    files = {OLD: {"Dockerfile": dockerfile("3.19")}, NEW: {"Dockerfile": dockerfile("3.20")}}
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert [(c["predicate"], c["old"], c["new"]) for c in out["changed"]] == [
        ("base-image", "alpine:3.19", "alpine:3.20")
    ]
    assert out["blocked"] and not out["fixed"]["blocked"]


def test_extract_reads_a_continued_docker_stage_at_end_of_file():
    dockerfile = "FROM alpine:3.19 AS build\nFROM alpine:3.20 \\\n"
    assert extract("Dockerfile", dockerfile) == [("base-image", "alpine:3.20")]


def test_docker_stage_inheriting_a_build_argument_stays_unknown():
    dockerfile = "ARG BASE=alpine\nFROM ${BASE} AS base\nFROM base AS final\n"
    assert extract("Dockerfile", dockerfile) == []


def test_a_clean_bounded_review_reports_files_it_did_not_read(monkeypatch):
    monkeypatch.setattr("mempr.realrepo.MAX_FILES", 1)
    root = "module root\n\ngo 1.22\n"
    files = {
        OLD: {"go.mod": root, "api/go.mod": root},
        NEW: {"go.mod": root, "api/go.mod": "module api\n\ngo 1.23\n", "worker/go.mod": root},
    }
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert out["files"] == {"old": 1, "head": 1}
    assert out["files_skipped"] == {"old": 1, "head": 2}
    assert not out["blocked"] and not out["fixed"]["blocked"]
    assert out["changed"] == out["unconfirmed"] == []


def test_previous_commit_uses_exact_first_parent_of_a_merge_tip():
    out = learn(
        "acme/shop",
        None,
        load_rules(None),
        fetch=lambda url: fake(url, parents=(OLD, "c" * 40)),
    )
    assert out["months"] is None
    assert (out["old"]["sha"], out["head"]["sha"]) == (OLD, NEW)
    assert out["blocked"] and not out["fixed"]["blocked"]
    assert all(f"/blob/{OLD}/" in f["old_url"] for f in out["changed"])


def test_previous_commit_on_a_one_commit_repo_explains_why_it_cannot_compare():
    with pytest.raises(RepoError, match="only one commit") as error:
        learn("acme/shop", None, load_rules(None), fetch=lambda url: fake(url, parents=()))
    assert error.value.status == 422


@pytest.mark.parametrize(
    "repo, months, status",
    [("not a repo", 12, 422), ("acme/shop", 0, 422), ("acme/missing", 12, 404)],
)
def test_learn_refuses_what_it_cannot_read(repo, months, status):
    with pytest.raises(RepoError) as error:
        learn(repo, months, load_rules(None), fetch=fake)
    assert error.value.status == status


@pytest.mark.parametrize(
    "visibility",
    [{"private": True}, {"private": False, "visibility": "internal"}, {}],
)
def test_learn_requires_public_metadata_before_reading_contents(visibility):
    def fetch_metadata(url):
        assert url == "https://api.github.com/repos/acme/shop"
        return 200, json.dumps({"default_branch": "main", **visibility}).encode()

    with pytest.raises(RepoError, match="no public repository") as error:
        learn("acme/shop", None, load_rules(None), fetch=fetch_metadata)
    assert error.value.status == 404


def test_a_value_whose_slug_matches_the_old_one_is_still_a_change():
    files = {
        OLD: {"pyproject.toml": '[project]\nrequires-python = ">=3.10"\n'},
        NEW: {"pyproject.toml": '[project]\nrequires-python = "~=3.10"\n'},
    }

    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert [(c["old"], c["new"]) for c in out["changed"]] == [(">=3.10", "~=3.10")]
    assert out["blocked"] and not out["fixed"]["blocked"]


@pytest.mark.parametrize("old_flat, head_flat", [(True, True), (False, True), (True, False)])
def test_distinct_folders_keep_separate_facts_and_sources(old_flat, head_flat):
    flat = "packages/core-ui/package.json"
    nested = "packages/core/ui/package.json"

    def manifest(value):
        return json.dumps({"engines": {"node": value}})

    files = {OLD: {nested: manifest(">=18")}, NEW: {nested: manifest(">=22")}}
    if old_flat:
        files[OLD][flat] = manifest(">=18")
    if head_flat:
        files[NEW][flat] = manifest(">=18")

    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    assert out["learned"] == 1 + old_flat
    assert out["proposed"] == 1 + (head_flat and not old_flat)
    assert out["blocked"] and out["rules"] == ["contradiction"]
    assert not out["fixed"]["blocked"]
    (change,) = out["changed"]
    old_url = f"https://github.com/acme/shop/blob/{OLD}/{nested}"
    new_url = f"https://github.com/acme/shop/blob/{NEW}/{nested}"
    assert (change["old"], change["new"]) == (">=18", ">=22")
    assert (change["old_url"], change["new_url"]) == (old_url, new_url)
    expected = {
        old_url,
        new_url,
        f"https://github.com/acme/shop/blob/{OLD if old_flat else NEW}/{flat}",
    }

    for graph in (out["after"], out["fixed_after"]):
        nodes = {n["id"]: n for n in graph["nodes"]}
        about = {r["source"]: r["target"] for r in graph["rels"] if r["type"] == "ABOUT"}
        sources = [r for r in graph["rels"] if r["type"] == "SOURCE"]
        assert len(sources) == len(expected)
        source_entities = {nodes[r["target"]]["props"]["uri"]: about[r["source"]] for r in sources}
        assert set(source_entities) == expected
        assert source_entities[old_url] == source_entities[new_url] == f"Entity/{change['entity']}"
        assert len(set(source_entities.values())) == 2

    assert len(out["unconfirmed"]) == int(old_flat and not head_flat)
    if out["unconfirmed"]:
        assert out["unconfirmed"][0]["path"] == flat
        assert out["unconfirmed"][0]["reason"] == "source-removed"


@pytest.mark.parametrize(
    ("head_files", "reason"),
    [
        ({"go.mod": "module root\n\ngo 1.23\n"}, "source-removed"),
        ({"go.mod": "module root\n\ngo 1.23\n", "api/go.mod": "go 1.22\n"}, "source-unread"),
        ({"api/go.mod": "module api\n"}, "fact-not-found"),
    ],
)
def test_unconfirmed_facts_keep_their_provenance_without_staying_active(
    head_files, reason, monkeypatch
):
    monkeypatch.setattr("mempr.realrepo.MAX_FILES", 1)
    files = {OLD: {"api/go.mod": "module api\n\ngo 1.22\n"}, NEW: head_files}
    out = learn("acme/shop", 12, load_rules(None), fetch=lambda url: fake(url, files=files))
    fact_id = "Fact/api--go-version--1-22"
    old_url = f"https://github.com/acme/shop/blob/{OLD}/api/go.mod"
    assert out["changed"] == []
    assert out["unconfirmed"] == [
        {
            "fact": fact_id.removeprefix("Fact/"),
            "entity": "api",
            "predicate": "go-version",
            "old": "1.22",
            "old_url": old_url,
            "path": "api/go.mod",
            "reason": reason,
        }
    ]
    assert not out["blocked"] and not out["fixed"]["blocked"]
    for graph, status in (
        (out["before"], "active"),
        (out["after"], "unconfirmed"),
        (out["fixed_after"], "unconfirmed"),
    ):
        nodes = {n["id"]: n for n in graph["nodes"]}
        assert nodes[fact_id]["props"]["status"] == status
        source = next(
            r["target"] for r in graph["rels"] if r["source"] == fact_id and r["type"] == "SOURCE"
        )
        assert nodes[source]["props"]["uri"] == old_url


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
