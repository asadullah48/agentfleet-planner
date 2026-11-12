import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentfleet.planner import plan


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
        result = plan(data)
        result["actions"][0]["request"]["cpu"] = 999
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
        for bad in cases:
            with self.subTest(snapshot=bad), self.assertRaises(ValueError):
                plan(bad)


class CLITests(unittest.TestCase):
    def run_cli(self, data, *args):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(data, encoding="utf-8")
            before = path.read_bytes()
            result = subprocess.run([sys.executable, "-m", "agentfleet.cli", str(path), *args], capture_output=True, text=True, encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
