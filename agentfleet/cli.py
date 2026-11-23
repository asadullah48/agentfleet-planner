"""CLI for reading one JSON snapshot and printing a proposed plan."""

import argparse
import json
import sys
from pathlib import Path

from .planner import plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline agent worker capacity planner; no infrastructure changes.")
    parser.add_argument("snapshot", type=Path, help="JSON snapshot with nodes, pools, workers")
    parser.add_argument("--json", action="store_true", help="Print structured plan")
    parser.add_argument("--trace", type=Path, metavar="FILE", help="Write one JSON line per planning decision to FILE (overwritten)")
    args = parser.parse_args(argv)
    if args.trace and args.trace.resolve() == args.snapshot.resolve():
        parser.error("--trace must not overwrite the input snapshot")
    trace: list[dict] = []
    try:
        result = plan(json.loads(args.snapshot.read_text(encoding="utf-8-sig")), trace)
        if args.trace:
            args.trace.write_text("".join(json.dumps(item) + "\n" for item in trace), encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("OFFLINE PLAN - no actions executed")
        for action in result["actions"]:
            print(f"{action['action'].upper():7} {action['worker']} pool={action['pool']} node={action['node']}")
        for blocked in result["blocked"]:
            print(f"BLOCKED {blocked['pool']}: {blocked['missing_replicas']} replica(s), {blocked['reason']}")
        print(f"{len(result['actions'])} proposed action(s); {len(result['blocked'])} blocked pool(s)")
    return 1 if result["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
