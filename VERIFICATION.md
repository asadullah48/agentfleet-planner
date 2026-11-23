# Verification

**VERIFIED: 2026-11-23 · implementation commit `0db5971ccbbaf61503fc695ac0e814848e900a9c`.**

Environment: Windows 11, Python 3.14. Local checks against the committed tree;
no production infrastructure.

From the repository root:

```text
python -X utf8 -m unittest discover -s tests
Ran 32 tests in 4.423s
OK

python -m pytest -q
32 passed, 1771 subtests passed in 5.72s

ruff check .
All checks passed!

mypy agentfleet
Success: no issues found in 3 source files
```

The 1,771 subtests are mostly the property test (400 seeded random snapshots,
checking kept + placed + shortfall == requested replicas per pool) and the
schema bad-input cases, which are cross-checked against the `jsonschema` library.

CLI with decision trace:

```text
python -X utf8 -m agentfleet.cli examples/fleet.json --trace trace.jsonl
exit code 1
trace.jsonl: 4 lines (3 place, 1 shortfall)
```

Exit code `1` is expected: the synthetic snapshot has no second GPU, so one
GPU replica is reported as a shortfall, and its trace line lists every
rejected node with need vs free. No placements were executed.

Not re-run on this date: the separate-venv wheel install check recorded on
2026-11-12 for commit `46dd87d`.

GitHub Actions results are separate from these local results; the new
lint / matrix / stdlib-only jobs have not yet run on GitHub as of this record.
