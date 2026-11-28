# What one relationship changes

The shipped Payments API example links the quarterly restore drill to the
nightly backup. The backup in turn links to the PostgreSQL 15 fact. To isolate
the contribution of that path, the replay copies the checked-in Markdown vault
twice and removes only the drill-to-backup `DERIVED_FROM` edge from one copy.
It verifies that all other graph facts match before running both conditions.

| Observed output | Edge present | Edge removed |
| --- | --- | --- |
| `postgres` keyword hits | Same two hits | Same two hits |
| Restore drill in connected recall | Two hops | Absent |
| Identical proposal to retract the nightly backup | Blocked by `stale-derivation` | Passes the default review |

The restore drill's note and source still exist in both copies. The reviewer
cannot flag an implied dependency that the vault never records. This is a
deterministic control using the shipped example. The paired model trials remain
a small sample, with one model and the same cases in each pair.

From a fresh clone, run this in the repository root with Python 3.10 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python demo/ab/relationship_control.py
```

If [Memory PR is already installed](../../docs/usage.md#run-it-locally), run
the last command in that environment.

The [recorded output](relationship-control.json) includes the exact removed edge,
unchanged property-level proposal and the path the reviewer reported.
