# Security

## The promise: it never changes infrastructure

AgentFleet Planner reads one local JSON snapshot and prints a plan. It has no credentials,
no infrastructure adapters, no network code, and no runtime dependencies.

- `agentfleet.planner.plan()` makes **no file writes, network calls, or subprocess calls**.
- The CLI writes exactly one file, and only when you pass `--trace FILE`. It refuses a
  trace path that is the same as the input snapshot.
- The input snapshot is never modified.

This is tested, not just stated. `NoSideEffectTests` in
[`tests/test_planner.py`](tests/test_planner.py) records CPython audit events (PEP 578) for
every file open, socket, subprocess, and filesystem change while the planner and the CLI run.
It asserts that none happen, apart from the requested trace file. A positive-control test
checks that the detector does catch a real write and a real socket.

## Input handling

Snapshots are untrusted input. They are checked against
[`snapshot.schema.json`](agentfleet/snapshot.schema.json) (unknown fields rejected), then
against unique names, valid references, and existing overcommit, before any planning. Errors
name the JSON path and exit with code `2`, without a traceback.

## Out of scope

A plan is not an authorization to act. Any adapter that executes a plan is outside this
project, and it must re-read live state and verify each release before placing workers.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository rather than a public
issue. This is a portfolio project with no SLA; reports are handled on a best-effort basis.
