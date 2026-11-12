# Verification

**VERIFIED: 2026-11-12 · implementation commit `46dd87d8b54019061ad429bfbaf18c7df919c173`.**

Environment: Windows, Python 3.14. Local checks; no production infrastructure.

From the repository root:

```text
python -X utf8 -m unittest discover -s tests -v

Ran 18 tests in 1.595s
OK
```

Built and installed the wheel into a separate virtual environment using:

```text
python -m pip install --no-build-isolation --no-deps ./outputs/agentfleet-planner
```

The environment was created with `--system-site-packages` to reuse the local
build tools. This was not a fully isolated dependency-installation test.

From `work/agentfleet`, outside the source tree:

```text
.\venv\Scripts\python.exe -X utf8 -m unittest discover -s ..\..\outputs\agentfleet-planner\tests -v

Ran 18 tests in 1.674s
OK
```

Installed console entry point:

```text
.\venv\Scripts\agentfleet.exe ..\..\outputs\agentfleet-planner\examples\fleet.json --json
```

Observed three proposed placements and one blocked GPU replica. Exit code `1`
was expected because the synthetic snapshot lacks a second GPU. No placements
were executed. CLI tests also verify that the input file is unchanged.

An initial test run failed because a Unicode dash in human output used a
Windows encoding inconsistent with the test reader. The output was changed
to ASCII and the complete suite was rerun successfully.

GitHub Actions results are separate from these local results.
