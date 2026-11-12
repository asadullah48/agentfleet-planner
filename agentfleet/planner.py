"""Validated snapshots and deterministic, non-mutating placement plans."""

from copy import deepcopy

RESOURCES = ("cpu", "memory_mib", "gpu")


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _resources(value, label):
    if not isinstance(value, dict) or set(value) != set(RESOURCES):
        raise ValueError(f"{label} must contain exactly {RESOURCES}")
    for resource in RESOURCES:
        _integer(value[resource], f"{label}.{resource}")
    if value["cpu"] == 0 or value["memory_mib"] == 0:
        raise ValueError(f"{label}: cpu and memory_mib must be positive")


def validate(snapshot):
    """Reject invalid, ambiguous or already overcommitted snapshots."""
    if not isinstance(snapshot, dict) or set(snapshot) != {"nodes", "pools", "workers"}:
        raise ValueError("snapshot must contain exactly nodes, pools, workers")
    for kind in ("nodes", "pools", "workers"):
        if not isinstance(snapshot[kind], list):
            raise ValueError(f"{kind} must be a list")
        names = set()
        for item in snapshot[kind]:
            if not isinstance(item, dict):
                raise ValueError(f"{kind} entries must be objects")
            _name(item.get("name"), f"{kind}.name")
            if item["name"] in names:
                raise ValueError(f"duplicate {kind} name: {item['name']}")
            names.add(item["name"])
    nodes = {n["name"]: n for n in snapshot["nodes"]}
    pools = {p["name"]: p for p in snapshot["pools"]}
    for node in nodes.values():
        if set(node) != {"name", "ready", "capacity"} or type(node["ready"]) is not bool:
            raise ValueError("node requires name, boolean ready, capacity")
        _resources(node["capacity"], "node.capacity")
    for pool in pools.values():
        if set(pool) != {"name", "replicas", "request"}:
            raise ValueError("pool requires name, replicas, request")
        _integer(pool["replicas"], "pool.replicas")
        _resources(pool["request"], "pool.request")
    used = {name: dict.fromkeys(RESOURCES, 0) for name in nodes}
    for worker in snapshot["workers"]:
        if set(worker) != {"name", "pool", "node", "request"}:
            raise ValueError("worker requires name, pool, node, request")
        _name(worker["pool"], "worker.pool")
        _name(worker["node"], "worker.node")
        if worker["pool"] not in pools or worker["node"] not in nodes:
            raise ValueError(f"worker {worker['name']} references unknown pool or node")
        _resources(worker["request"], "worker.request")
        for resource in RESOURCES:
            used[worker["node"]][resource] += worker["request"][resource]
    for name in nodes:
        if any(used[name][r] > nodes[name]["capacity"][r] for r in RESOURCES):
            raise ValueError(f"node {name} is already over capacity")


def plan(snapshot):
    """Plan releases, then first-fit placements; never execute the actions.

    Pool/node/worker sorting makes results independent of input list ordering.
    A resource-drifted worker or worker on an unready node is replaced.
    """
    validate(snapshot)
    nodes = {n["name"]: n for n in sorted(snapshot["nodes"], key=lambda n: n["name"])}
    pools = sorted(snapshot["pools"], key=lambda p: p["name"])
    free = {name: deepcopy(node["capacity"]) for name, node in nodes.items()}
    actions, blocked = [], []
    kept = {p["name"]: [] for p in pools}
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
            if reason:
                actions.append({"action": "release", "worker": worker["name"], "pool": pool["name"], "node": worker["node"], "reason": reason})
            else:
                kept[pool["name"]].append(worker)
                for r in RESOURCES:
                    free[worker["node"]][r] -= worker["request"][r]
    # Releases above are assumed successful for planning purposes only.
    for pool in pools:
        missing = pool["replicas"] - len(kept[pool["name"]])
        for _ in range(missing):
            chosen = next((name for name, node in nodes.items() if node["ready"] and all(free[name][r] >= pool["request"][r] for r in RESOURCES)), None)
            if chosen is None:
                blocked.append({"pool": pool["name"], "missing_replicas": missing, "reason": "insufficient_ready_capacity", "request": deepcopy(pool["request"])})
                break
            index = 1
            while f"{pool['name']}-{index}" in names:
                index += 1
            worker_name = f"{pool['name']}-{index}"
            names.add(worker_name)
            actions.append({"action": "place", "worker": worker_name, "pool": pool["name"], "node": chosen, "request": deepcopy(pool["request"])})
            for r in RESOURCES:
                free[chosen][r] -= pool["request"][r]
            missing -= 1
    return {"mode": "offline_plan", "converged": not actions and not blocked, "actions": actions, "blocked": blocked, "remaining_capacity": free}
