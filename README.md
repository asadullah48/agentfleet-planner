<div align="center">

# AgentFleet Planner

**Declare the worker pools. Inspect the capacity. Explain the plan.**

Offline desired-state planning for agent infrastructure.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.1.0-0f766e?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-475569?style=flat-square)

[Quick start](#quick-start) · [Engineering evidence](#engineering-evidence) · [Limits](#limits) · [Portfolio](https://asadullahshafique-devunity.vercel.app/)

</div>

---

## The problem

An agent service may need API workers on CPU nodes and model workers on GPU nodes. A desired replica count alone does not show whether those workers fit, which existing workers have drifted, or why a request is blocked.

AgentFleet Planner compares desired pools with a local snapshot of nodes and workers. It produces proposed releases and placements, with capacity accounting and explicit shortfalls. Every run is a plan; it never changes infrastructure.

## Inspiration and original scope

[NVKE](https://github.com/dsx-ai-factory/nvke) provides a management plane for GPU Kubernetes infrastructure. This project borrows the separation between management intent and workload resources.

The implementation here is original Python code. No NVKE source was copied or cloned. It is an offline worker-pool planner, with no Kubernetes controllers, cluster provisioning, NVIDIA integration, or NVKE affiliation.

## Quick start

Requires Python 3.10 or newer. Download this repository's ZIP, extract it, and run from the project directory:

```bash
python -m pip install .
agentfleet examples/fleet.json
agentfleet examples/fleet.json --json
```

The bundled example is **synthetic**. It requests two CPU workers and two GPU workers, but has capacity for only one GPU worker. The plan proposes three placements and reports one missing replica. Its exit code is `1` because capacity is insufficient.

## The engineering approach

| Stage | What happens | Source |
| --- | --- | --- |
| 01 · Validate | Reject duplicate names, invalid resources, broken references, and existing overcommit. | [`planner.py`](agentfleet/planner.py) |
| 02 · Compare | Keep healthy matching workers; propose releasing excess, drifted, or unready-node workers. | [`planner.py`](agentfleet/planner.py) |
| 03 · Place | Use sorted pool and node names with a deterministic first-fit policy. | [`planner.py`](agentfleet/planner.py) |
| 04 · Explain | Return proposed actions, missing replica counts, and remaining capacity. | [`cli.py`](agentfleet/cli.py) |

Releases are planned before placements. Capacity from a proposed release is available to later placements **only under the assumption that the release succeeds**. An infrastructure adapter would need to verify completion and re-read state before executing any placement.

## Snapshot contract

See [`examples/fleet.json`](examples/fleet.json) for the complete input.

| Collection | Required fields |
| --- | --- |
| `nodes` | Unique `name`, boolean `ready`, `capacity` |
| `pools` | Unique `name`, non-negative integer `replicas`, `request` |
| `workers` | Unique `name`, existing `pool`, existing `node`, actual `request` |

Every resource object contains exactly `cpu`, `memory_mib`, and `gpu`. CPU and memory are positive integers; GPU count is a non-negative integer. Fractional CPU and GPU requests are unsupported. Worker requests record observed reservations, so resource drift can be compared with pool intent.

The input snapshot stays unchanged. Unknown fields are rejected to catch misspelled configuration. Pools and nodes are processed in name order; an earlier pool can consume capacity before a later one.

### Output and exit codes

JSON contains `mode`, `converged`, `actions`, `blocked`, and `remaining_capacity`. `converged` is true only when no actions or shortfalls remain. Remaining capacity includes unready nodes, but those nodes cannot receive placements.

| Code | Meaning |
| --- | --- |
| `0` | All desired replicas can be planned, or the snapshot is already converged. |
| `1` | At least one pool has missing replicas after placement. |
| `2` | Invalid input, unreadable file, or command usage error. |

A successful plan is not evidence of a successful deployment.

## Engineering evidence

Local verification results are recorded in [`VERIFICATION.md`](VERIFICATION.md), including the date, command, and exact test summary. The suite checks resource limits, resource drift, scale-down, unavailable nodes, deterministic ordering, input preservation, projected convergence, invalid snapshots, and CLI exit codes.

```bash
python -X utf8 -m unittest discover -s tests -v
```

[`Tests`](.github/workflows/test.yml) defines a GitHub Actions matrix for Python 3.10, 3.12, and 3.14. Its presence is not a passing CI claim; check the actual workflow run.

## Limits

- Offline snapshot planner: no live discovery, agent execution, model calls, or reconciliation loop.
- First-fit placement is deterministic but does not optimize utilization or guarantee a globally feasible assignment. It can report a shortfall where a different placement order would fit.
- CPU, memory, and whole-GPU counts only; no GPU models, topology, affinity, quotas, or tenancy enforcement.
- Proposed worker names are local to the snapshot, with no distributed ID allocation.
- No health probes, persistent state, authentication, or infrastructure credentials.
- No production customers, scale benchmarks, or operational reliability claims.

## Development

The runtime and tests use the Python standard library. Setuptools is used for packaging.

```bash
python -m pip install .
python -X utf8 -m unittest discover -s tests -v
```

## Built by Asadullah Shafique

Agentic AI Systems Engineer · Karachi, Pakistan

I build agent tooling with explicit controls, inspectable behavior, and tests tied to the implementation.

[Portfolio](https://asadullahshafique-devunity.vercel.app/) · [GitHub](https://github.com/asadullah48) · [Discuss an agent engineering project](mailto:asadullahshafique@hotmail.com)

## License

[MIT](LICENSE)
