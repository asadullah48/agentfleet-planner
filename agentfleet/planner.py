"""Validated snapshots and deterministic, non-mutating placement plans."""

import json
import re
from copy import deepcopy
from pathlib import Path

RESOURCES = ("cpu", "memory_mib", "gpu")
SCHEMA = json.loads(Path(__file__).with_name("snapshot.schema.json").read_text(encoding="utf-8"))
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}


def _check(value, schema, path):
    """Apply the subset of JSON Schema used by snapshot.schema.json (stdlib only)."""
    if "$ref" in schema:
        schema = SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    kind = schema.get("type")
    # bool is an int subclass in Python, but not a JSON integer.
    if kind and (not isinstance(value, _TYPES[kind]) or (kind == "integer" and isinstance(value, bool))):
        raise ValueError(f"{path}: expected {kind}, got {type(value).__name__} {value!r}")
    if "minimum" in schema and value < schema["minimum"]:
        raise ValueError(f"{path}: must be >= {schema['minimum']}, got {value}")
    if "pattern" in schema and not re.search(schema["pattern"], value):
        raise ValueError(f"{path}: must match pattern {schema['pattern']!r}, got {value!r}")
    if kind == "object":
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ValueError(f"{path}: missing required field(s): {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(schema["properties"]))
            if unknown:
                raise ValueError(f"{path}: unknown field(s): {', '.join(map(str, unknown))}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}")
    if kind == "array":
        for index, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{index}]")


def validate(snapshot):
    """Check the schema, then unique names, references, and existing overcommit."""
    _check(snapshot, SCHEMA, "$")
    for kind in ("nodes", "pools", "workers"):
        seen = set()
        for index, item in enumerate(snapshot[kind]):
            if item["name"] in seen:
                raise ValueError(f"$.{kind}[{index}].name: duplicate name {item['name']!r}")
            seen.add(item["name"])
    nodes = {n["name"]: n for n in snapshot["nodes"]}
    pools = {p["name"] for p in snapshot["pools"]}
    used = {name: dict.fromkeys(RESOURCES, 0) for name in nodes}
    for index, worker in enumerate(snapshot["workers"]):
        for field, known in (("pool", pools), ("node", nodes)):
            if worker[field] not in known:
                raise ValueError(f"$.workers[{index}].{field}: unknown {field} {worker[field]!r}")
        for r in RESOURCES:
            used[worker["node"]][r] += worker["request"][r]
    for name, node in nodes.items():
        over = [r for r in RESOURCES if used[name][r] > node["capacity"][r]]
        if over:
            raise ValueError(f"node {name!r} is already over capacity on {', '.join(over)}")


def plan(snapshot, trace=None):
    """Plan releases, then first-fit placements; never execute the actions.

    Pool/node/worker sorting makes results independent of input list ordering.
    A resource-drifted worker or worker on an unready node is replaced.
    If `trace` is a list, one decision record per keep/release/place/shortfall is appended.
    """
    validate(snapshot)

    def record(item):
        if trace is not None:
            trace.append(deepcopy(item))  # never alias snapshot objects into the trace

    nodes = {n["name"]: n for n in sorted(snapshot["nodes"], key=lambda n: n["name"])}
    pools = sorted(snapshot["pools"], key=lambda p: p["name"])
    free = {name: deepcopy(node["capacity"]) for name, node in nodes.items()}
    actions: list[dict] = []
    blocked: list[dict] = []
    kept: dict[str, list[dict]] = {p["name"]: [] for p in pools}
    names = {w["name"] for w in snapshot["workers"]}
    for pool in pools:
        workers = sorted((w for w in snapshot["workers"] if w["pool"] == pool["name"]), key=lambda w: w["name"])
        for worker in workers:
            reason = None
            if not nodes[worker["node"]]["ready"]:
                reason = "node_unready"
            elif worker["request"] != pool["request"]:
                reason = "resource_drift"
            elif len(kept[pool["name"]]) >= pool["replicas"]:
                reason = "excess_replica"
            base = {"worker": worker["name"], "pool": pool["name"], "node": worker["node"]}
            if reason:
                actions.append({"action": "release", **base, "reason": reason})
                record({"decision": "release", **base, "reason": reason, "observed": worker["request"], "desired": pool["request"]})
            else:
                kept[pool["name"]].append(worker)
                record({"decision": "keep", **base, "reason": "healthy_match"})
                for r in RESOURCES:
                    free[worker["node"]][r] -= worker["request"][r]

    def rejection(name, request):
        if not nodes[name]["ready"]:
            return {"node": name, "reason": "node_unready"}
        short = {r: {"need": request[r], "free": free[name][r]} for r in RESOURCES if free[name][r] < request[r]}
        return {"node": name, "reason": "insufficient_capacity", "short": short} if short else None

    # Releases above are assumed successful for planning purposes only.
    for pool in pools:
        missing = pool["replicas"] - len(kept[pool["name"]])
        while missing:
            rejected, chosen = [], None
            for name in nodes:
                why = rejection(name, pool["request"])
                if why is None:
                    chosen = name
                    break
                rejected.append(why)
            if chosen is None:
                blocked.append({"pool": pool["name"], "missing_replicas": missing, "reason": "insufficient_ready_capacity", "request": deepcopy(pool["request"])})
                record({"decision": "shortfall", "pool": pool["name"], "missing_replicas": missing, "request": pool["request"], "rejected_nodes": rejected})
                break
            index = 1
            while f"{pool['name']}-{index}" in names:
                index += 1
            worker_name = f"{pool['name']}-{index}"
            names.add(worker_name)
            actions.append({"action": "place", "worker": worker_name, "pool": pool["name"], "node": chosen, "request": deepcopy(pool["request"])})
            record({"decision": "place", "worker": worker_name, "pool": pool["name"], "node": chosen, "reason": "first_fit", "rejected_nodes": rejected})
            for r in RESOURCES:
                free[chosen][r] -= pool["request"][r]
            missing -= 1
    return {"mode": "offline_plan", "converged": not actions and not blocked, "actions": actions, "blocked": blocked, "remaining_capacity": free}
