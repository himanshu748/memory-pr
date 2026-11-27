# Set up the GitHub Action

Add `memory-review` to a repository that keeps its shared agent memory in Git.
The job compares the pull request and its proposed merge with the base branch.
It reports the graph changes and fails if the merge would damage the graph.

## 1. Commit a memory vault

Keep the Relate Markdown notes under `memory/`. To start a vault, run a command
like this from your repository after [installing the CLI](usage.md#run-it-locally).
Use the actual value, author, and committed source from your project.

```bash
mempr add-fact --entity payments-api --predicate database --value mongodb-8 \
  --agent maintainer --source 'https://github.com/OWNER/REPO/blob/COMMIT/path/to/source'
git add memory/
git commit -m 'Seed agent memory'
```

The [graph schema](how-it-works.md#the-graph) shows which notes and relationships
the command creates. If you already have a vault at a different path, set `vault`
in the workflow. Get the vault onto the branch you want to protect.

## 2. Add the workflow

Save this as `.github/workflows/memory-review.yml` and commit it.

```yaml
name: memory-review
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  memory-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: himanshu748/memory-pr@v1
```

Full Git history lets the action find the common ancestor and inspect what Git
would merge. On a pull request the action picks the base branch and the PR head
commit from the event. It updates one review comment and also writes the review
to the job summary. For fork pull requests, GitHub can restrict the token to
read-only access. The check still runs after any required workflow approval, and
the review is available in the job summary. No extra GitHub secret is needed.

| Input | Default | When to set it |
| --- | --- | --- |
| `vault` | `memory` | The Markdown vault lives at a different repository-relative path. |
| `rules` | `memory-rules.yaml` | Load additional rules from another path on the base branch. |
| `base` and `head` | Pull request base and head | Select refs explicitly when running outside a pull request. Without an event, the defaults are `origin/main` and `HEAD`. |
| `comment` | `true` | Set to `"false"` for review output only in the job summary. You can then omit `pull-requests: write`. |
| `github-token` | `${{ github.token }}` | Supply a different token only if your repository requires it for comments. |
| `python-version` | `3.12` | Select a different supported Python version on the runner. |

## 3. Verify and require the check

1. Open a pull request that adds a properly sourced fact for a new entity or
   predicate. Look for a green **memory-review** job and a passed review in its summary.
2. Open a test pull request that adds a second active value for an existing
   single-value fact. The failing review should name both values. You can first
   run `mempr check --base origin/main --head HEAD` locally, substituting your
   default branch for `main` if needed.
3. In the destination branch's GitHub ruleset or branch protection rule, enable
   **Require status checks to pass**. Select the `memory-review` job from your
   repository's recent checks, then save the rule. Confirm that the failing test
   pull request is blocked and close it when finished.

The CLI exits `0` for a passed review, `1` for blocking findings, and `2` when
the review cannot run. If it cannot find the base, head, or common ancestor,
check the refs and the checkout's `fetch-depth` before rerunning.

This project's [own workflow](../.github/workflows/memory-review.yml) also
handles demo PRs opened by automation. Its extra steps are specific to that
demo. The workflow above covers pull requests that trigger GitHub's
`pull_request` event in your repository.
