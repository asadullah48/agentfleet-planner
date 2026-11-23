import contextlib
import copy
import io
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentfleet.cli import main
from agentfleet.planner import RESOURCES, SCHEMA, plan

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "fleet.json"


def resources(cpu=2, memory=1024, gpu=0):
    return {"cpu": cpu, "memory_mib": memory, "gpu": gpu}


def snapshot(replicas=1):
    return {"nodes": [{"name": "node-a", "ready": True, "capacity": resources(4, 4096, 1)}],
            "pools": [{"name": "agent", "replicas": replicas, "request": resources()}], "workers": []}


def worker(name="agent-1", request=None):
    return {"name": name, "pool": "agent", "node": "node-a", "request": request or resources()}


class PlannerTests(unittest.TestCase):
    def test_empty_fleet_converges(self):
        self.assertTrue(plan({"nodes": [], "pools": [], "workers": []})["converged"])

    def test_place_with_capacity_accounting(self):
        result = plan(snapshot(2))
        self.assertEqual(len(result["actions"]), 2)
        self.assertEqual(result["remaining_capacity"]["node-a"], resources(0, 2048, 1))

    def test_shortfall_count(self):
        self.assertEqual(plan(snapshot(5))["blocked"][0]["missing_replicas"], 3)

    def test_memory_blocks(self):
        data = snapshot()
        data["pools"][0]["request"]["memory_mib"] = 8192
        self.assertEqual(plan(data)["actions"], [])
        self.assertTrue(plan(data)["blocked"])

    def test_gpu_blocks(self):
        data = snapshot(2)
        data["pools"][0]["request"]["gpu"] = 1
        self.assertEqual(plan(data)["blocked"][0]["missing_replicas"], 1)

    def test_healthy_worker_converges(self):
        data = snapshot()
        data["workers"] = [worker()]
        self.assertTrue(plan(data)["converged"])

    def test_scale_down(self):
        data = snapshot(0)
        data["workers"] = [worker()]
        self.assertEqual(plan(data)["actions"][0]["reason"], "excess_replica")

    def test_drift_replaces_using_released_capacity(self):
        data = snapshot()
        data["workers"] = [worker(request=resources(4))]
        result = plan(data)
        self.assertEqual([a["action"] for a in result["actions"]], ["release", "place"])
        self.assertEqual(result["actions"][0]["reason"], "resource_drift")
        self.assertEqual(result["actions"][1]["worker"], "agent-2")

    def test_unready_node_not_reused(self):
        data = snapshot()
        data["nodes"][0]["ready"] = False
        data["workers"] = [worker()]
        result = plan(data)
        self.assertEqual(result["actions"][0]["reason"], "node_unready")
        self.assertTrue(result["blocked"])

    def test_plan_does_not_mutate_input(self):
        data = snapshot(3)
        original = copy.deepcopy(data)
        trace = []
        result = plan(data, trace)
        result["actions"][0]["request"]["cpu"] = 999
        trace[-1]["request"]["cpu"] = 999
        self.assertEqual(data, original)

    def test_order_independent(self):
        data = snapshot(3)
        data["nodes"].append({"name": "node-b", "ready": True, "capacity": resources(4, 4096, 1)})
        expected = plan(data)
        data["nodes"].reverse()
        self.assertEqual(plan(data), expected)

    def test_replanning_projected_state_converges(self):
        data = snapshot(2)
        for action in plan(data)["actions"]:
            data["workers"].append({"name": action["worker"], "pool": action["pool"], "node": action["node"], "request": action["request"]})
        self.assertTrue(plan(data)["converged"])

    def test_rejects_bad_inputs(self):
        cases = []
        cases.extend([None, [], {}, {"nodes": {}, "pools": [], "workers": []}])
        for key, value in [("replicas", -1), ("replicas", True), ("replicas", 1.5)]:
            data = snapshot()
            data["pools"][0][key] = value
            cases.append(data)
        for value in [-1, True, "2", 0]:
            data = snapshot()
            data["pools"][0]["request"]["cpu"] = value
            cases.append(data)
        for kind in ["nodes", "pools"]:
            data = snapshot()
            data[kind].append(copy.deepcopy(data[kind][0]))
            cases.append(data)
        for field in ["pool", "node"]:
            data = snapshot()
            data["workers"] = [worker()]
            data["workers"][0][field] = "unknown"
            cases.append(data)
        data = snapshot()
        data["workers"] = [worker(request=resources(9))]
        cases.append(data)
        data = snapshot()
        data["nodes"][0]["ready"] = "yes"
        cases.append(data)
        for bad_input in cases:
            with self.subTest(snapshot=bad_input), self.assertRaises(ValueError):
                plan(bad_input)


class CLITests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "agentfleet.cli", *map(str, args)], check=False, capture_output=True, text=True, encoding="utf-8")

    def run_cli(self, data, *args):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(data, encoding="utf-8")
            before = path.read_bytes()
            result = self.cli(path, *args)
            self.assertEqual(path.read_bytes(), before)
            return result

    def test_json_output(self):
        result = self.run_cli(json.dumps(snapshot()), "--json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["mode"], "offline_plan")

    def test_blocked_exit(self):
        result = self.run_cli(json.dumps(snapshot(3)), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["blocked"][0]["missing_replicas"], 1)

    def test_human_output(self):
        result = self.run_cli(json.dumps(snapshot()))
        self.assertEqual(result.returncode, 0)
        self.assertIn("no actions executed", result.stdout)

    def test_malformed_json(self):
        result = self.run_cli("{")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_schema(self):
        self.assertEqual(self.run_cli('{"nodes": 2}').returncode, 2)

    def test_invalid_input_error_names_the_field(self):
        data = snapshot()
        data["pools"][0]["replicas"] = -1
        result = self.run_cli(json.dumps(data))
        self.assertEqual(result.returncode, 2)
        self.assertIn("$.pools[0].replicas: must be >= 0, got -1", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_trace_file_is_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "trace.jsonl"
            self.assertEqual(self.cli(EXAMPLE, "--trace", out).returncode, 1)
            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["decision"] for r in records], ["place", "place", "place", "shortfall"])

    def test_trace_refuses_to_overwrite_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            before = path.read_bytes()
            result = self.cli(path, "--trace", path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must not overwrite", result.stderr)
            self.assertEqual(path.read_bytes(), before)


# (mutation of a snapshot with one healthy worker, expected error fragment)
SHAPE_ERRORS = [
    (lambda d: d["pools"][0].__setitem__("replicas", -1), "$.pools[0].replicas: must be >= 0, got -1"),
    (lambda d: d["pools"][0].__setitem__("replicas", True), "$.pools[0].replicas: expected integer, got bool True"),
    (lambda d: d["pools"][0].__setitem__("replicas", 1.5), "$.pools[0].replicas: expected integer, got float 1.5"),
    (lambda d: d["pools"][0]["request"].__setitem__("cpu", "2"), "$.pools[0].request.cpu: expected integer, got str '2'"),
    (lambda d: d["pools"][0]["request"].__setitem__("cpu", 0), "$.pools[0].request.cpu: must be >= 1, got 0"),
    (lambda d: d["pools"][0]["request"].pop("gpu"), "$.pools[0].request: missing required field(s): gpu"),
    (lambda d: d["nodes"][0].__setitem__("ready", "yes"), "$.nodes[0].ready: expected boolean, got str 'yes'"),
    (lambda d: d["nodes"][0].__setitem__("replcias", 2), "$.nodes[0]: unknown field(s): replcias"),
    (lambda d: d["workers"][0].__setitem__("name", "  "), "$.workers[0].name: must match pattern"),
    (lambda d: d.__setitem__("nodes", {}), "$.nodes: expected array, got dict {}"),
    (lambda d: d.pop("workers"), "$: missing required field(s): workers"),
]
RELATION_ERRORS = [
    (lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0])), "$.nodes[1].name: duplicate name 'node-a'"),
    (lambda d: d["workers"][0].__setitem__("pool", "ghost"), "$.workers[0].pool: unknown pool 'ghost'"),
    (lambda d: d["workers"][0].__setitem__("node", "ghost"), "$.workers[0].node: unknown node 'ghost'"),
    (lambda d: d["workers"][0].__setitem__("request", resources(9)), "node 'node-a' is already over capacity on cpu"),
]


def bad(mutate):
    data = snapshot()
    data["workers"] = [worker()]
    mutate(data)
    return data


class SchemaTests(unittest.TestCase):
    def test_errors_name_the_offending_path(self):
        for mutate, message in SHAPE_ERRORS + RELATION_ERRORS:
            with self.subTest(message=message), self.assertRaises(ValueError) as caught:
                plan(bad(mutate))
            self.assertIn(message, str(caught.exception))

    def test_schema_uses_only_keywords_the_stdlib_checker_implements(self):
        supported = {"$schema", "$id", "title", "description", "type", "required", "additionalProperties",
                     "properties", "items", "$defs", "$ref", "minimum", "pattern"}

        def walk(node):
            self.assertLessEqual(set(node), supported)
            children = [*node.get("properties", {}).values(), *node.get("$defs", {}).values()]
            for child in children + ([node["items"]] if "items" in node else []):
                walk(child)

        walk(SCHEMA)

    def test_stdlib_checker_agrees_with_jsonschema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (pip install .[dev])")
        validator = jsonschema.Draft202012Validator(SCHEMA)
        validator.check_schema(SCHEMA)
        for valid in [json.loads(EXAMPLE.read_text(encoding="utf-8")), bad(lambda d: None)]:
            self.assertEqual(list(validator.iter_errors(valid)), [])
        for mutate, message in SHAPE_ERRORS:
            with self.subTest(message=message):
                self.assertFalse(validator.is_valid(bad(mutate)))
        for mutate, message in RELATION_ERRORS:  # beyond JSON Schema; only planner.validate catches these
            with self.subTest(message=message):
                self.assertTrue(validator.is_valid(bad(mutate)))


def random_snapshot(rng):
    """A valid snapshot with random readiness, drift and capacity, never pre-overcommitted."""
    nodes = [{"name": f"n{i}", "ready": rng.random() < 0.8, "capacity": resources(rng.randint(1, 16), rng.randint(1, 8) * 1024, rng.randint(0, 2))}
             for i in range(rng.randint(0, 4))]
    pools = [{"name": f"p{i}", "replicas": rng.randint(0, 5), "request": resources(rng.randint(1, 4), rng.randint(1, 4) * 512, rng.randint(0, 1))}
             for i in range(rng.randint(0, 3))]
    used = {n["name"]: dict.fromkeys(RESOURCES, 0) for n in nodes}
    workers = []
    for i in range(rng.randint(0, 8) if nodes and pools else 0):
        node, pool = rng.choice(nodes), rng.choice(pools)
        request = dict(pool["request"]) if rng.random() < 0.7 else resources(rng.randint(1, 4), 512, 0)
        if all(used[node["name"]][r] + request[r] <= node["capacity"][r] for r in RESOURCES):
            workers.append({"name": f"w{i}", "pool": pool["name"], "node": node["name"], "request": request})
            for r in RESOURCES:
                used[node["name"]][r] += request[r]
    for items in (nodes, pools, workers):
        rng.shuffle(items)
    return {"nodes": nodes, "pools": pools, "workers": workers}


SEEDS = range(400)  # fixed seeds: reproducible, and a failing seed is printed in the subTest


class PropertyTests(unittest.TestCase):
    def test_placed_plus_shortfall_equals_requested(self):
        for seed in SEEDS:
            data = random_snapshot(random.Random(seed))
            result = plan(data)
            released = {a["worker"] for a in result["actions"] if a["action"] == "release"}
            missing = {b["pool"]: b["missing_replicas"] for b in result["blocked"]}
            for pool in data["pools"]:
                kept = sum(1 for w in data["workers"] if w["pool"] == pool["name"] and w["name"] not in released)
                placed = sum(1 for a in result["actions"] if a["action"] == "place" and a["pool"] == pool["name"])
                with self.subTest(seed=seed, pool=pool["name"]):
                    self.assertEqual(kept + placed + missing.get(pool["name"], 0), pool["replicas"])

    def test_remaining_capacity_never_negative(self):
        for seed in SEEDS:
            result = plan(random_snapshot(random.Random(seed)))
            for free in result["remaining_capacity"].values():
                with self.subTest(seed=seed):
                    self.assertTrue(all(v >= 0 for v in free.values()), free)


class TraceTests(unittest.TestCase):
    def assert_trace_explains(self, data):
        trace = []
        result = plan(data, trace)
        nodes = {n["name"]: n for n in data["nodes"]}
        shortfalls = {r["pool"]: r for r in trace if r["decision"] == "shortfall"}
        self.assertEqual(len(shortfalls), len(result["blocked"]))
        for blocked in result["blocked"]:
            record = shortfalls[blocked["pool"]]
            self.assertEqual(record["missing_replicas"], blocked["missing_replicas"])
            # Every node is accounted for, with a reason that really excludes it.
            self.assertEqual(sorted(n["node"] for n in record["rejected_nodes"]), sorted(nodes))
            for rejected in record["rejected_nodes"]:
                if rejected["reason"] == "node_unready":
                    self.assertFalse(nodes[rejected["node"]]["ready"])
                else:
                    self.assertEqual(rejected["reason"], "insufficient_capacity")
                    self.assertTrue(rejected["short"])
                    for r, gap in rejected["short"].items():
                        self.assertEqual(gap["need"], blocked["request"][r])
                        self.assertLess(gap["free"], gap["need"])
        # Every existing worker and every proposed action has exactly one decision record.
        decided = [(r["decision"], r["worker"]) for r in trace if "worker" in r]
        placed = [a["worker"] for a in result["actions"] if a["action"] == "place"]
        self.assertEqual(sorted(w for _, w in decided), sorted([w["name"] for w in data["workers"]] + placed))
        self.assertEqual(sorted(w for d, w in decided if d != "keep"), sorted(a["worker"] for a in result["actions"]))

    def test_trace_explains_every_shortfall(self):
        self.assert_trace_explains(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        unready = snapshot()
        unready["nodes"][0]["ready"] = False
        self.assert_trace_explains(unready)
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assert_trace_explains(random_snapshot(random.Random(seed)))

    def test_trace_does_not_change_the_plan(self):
        for seed in range(20):
            data = random_snapshot(random.Random(seed))
            self.assertEqual(plan(data, []), plan(data))


# Audit hooks (PEP 578) see every file open, socket and subprocess at the C level.
# They cannot be removed, so this one records only inside `recorded()`.
_EVENTS = None


def _audit(event, args):
    if _EVENTS is not None:
        _EVENTS.append((event, args))


sys.addaudithook(_audit)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_SIDE_EFFECT_EVENTS = ("socket.", "http.", "urllib.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
                       "os.startfile", "os.remove", "os.rename", "os.replace", "os.mkdir", "os.rmdir", "os.truncate",
                       "os.chmod", "os.link", "os.symlink", "shutil.")


@contextlib.contextmanager
def recorded():
    global _EVENTS
    _EVENTS = found = []
    try:
        yield found
    finally:
        _EVENTS = None


def side_effects(events):
    effects = []
    for event, args in events:
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and set(mode) & set("wax+")) or (mode is None and flags & _WRITE_FLAGS):
                effects.append(("write", str(path)))
        elif event.startswith(_SIDE_EFFECT_EVENTS):
            effects.append((event, None))
    return effects


class NoSideEffectTests(unittest.TestCase):
    def test_detector_catches_writes_and_sockets(self):
        """Positive control: without this, a broken detector would make the tests below pass vacuously."""
        with tempfile.TemporaryDirectory() as directory, recorded() as events:
            (Path(directory) / "x").write_text("x")
            socket.socket().close()
        kinds = {kind for kind, _ in side_effects(events)}
        self.assertIn("write", kinds)
        self.assertTrue(any(k.startswith("socket.") for k in kinds), kinds)

    def test_planner_makes_no_writes_or_network_calls(self):
        snapshots = [json.loads(EXAMPLE.read_text(encoding="utf-8"))] + [random_snapshot(random.Random(s)) for s in SEEDS]
        with recorded() as events:
            for data in snapshots:
                plan(data, [])
        self.assertEqual(side_effects(events), [])

    def test_cli_without_trace_makes_no_writes_or_network_calls(self):
        with recorded() as events, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(EXAMPLE), "--json"]), 1)
            self.assertEqual(main([str(EXAMPLE)]), 1)
        self.assertEqual(side_effects(events), [])

    def test_cli_trace_writes_only_the_trace_file(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "trace.jsonl"
            with recorded() as events, contextlib.redirect_stdout(io.StringIO()):
                main([str(EXAMPLE), "--trace", str(out)])
        self.assertEqual(side_effects(events), [("write", str(out))])


if __name__ == "__main__":
    unittest.main()
