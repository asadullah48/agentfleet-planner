# Verification

This file is committed **after** the code it describes. The code under test is
commit `0db5971ccbbaf61503fc695ac0e814848e900a9c`; the later commit `ba14aad`
changed only this file, and this file's own commit comes after both. No planner
code changed between `0db5971` and the commit that adds this record.

## VERIFIED

Things run or observed on **2026-11-23**, on a checkout of `main` at `ba14aad`.

### Local run

Environment: Windows 11, Python 3.14.7, ruff 0.15.12, mypy 2.4.1 (run via `uvx`,
in its own isolated environment).

```text
ruff check .
All checks passed!

mypy agentfleet
Success: no issues found in 3 source files

python -m pytest -q
32 passed, 1771 subtests passed in 3.49s

python -X utf8 -m unittest discover -s tests
Ran 32 tests in 2.829s
OK
```

### GitHub Actions

Workflow `CI` (`.github/workflows/test.yml`), run `45128178367`, push to `main`,
commit `ba14aad26f352e07db4f848893f926a3351bb7aa`, 2026-11-23T07:01:49Z.
Result: success.

| Job | Result |
|---|---|
| lint (ruff + mypy) | success |
| test (3.10) | success |
| test (3.12) | success |
| test (3.14) | success |
| stdlib-only (no extras, unittest) | success |

The README CI badge points to this workflow and showed "CI - passing" on
2026-11-23.

### Repository settings

Private vulnerability reporting enabled, checked 23 Nov 2026, output seen: enabled:true

## REPORTED / HISTORICAL

Earlier claims, not re-run on 2026-11-23:

- 2026-11-12, commit `46dd87d`: 18 tests passed locally; the wheel was built and
  installed into a separate venv (created with `--system-site-packages`, so not a
  fully isolated install), and the installed `agentfleet` entry point produced
  three placements and one blocked GPU replica on `examples/fleet.json`.
- Earlier on 2026-11-23, `python -m agentfleet.cli examples/fleet.json --trace`
  exited with code 1 and wrote 4 trace lines (3 place, 1 shortfall). That run was
  not repeated for this record.

## NOT CLAIMED

- No production use, no real customers, no real infrastructure. All inputs are
  synthetic snapshots such as `examples/fleet.json`.
- The planner proposes placements only; it executes nothing. The no-writes and
  no-network test covers the planner function, not any adapter that might act on
  a plan.
- Only these platforms were checked: Windows locally, and Ubuntu on GitHub Actions.
