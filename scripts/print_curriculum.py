from __future__ import annotations

import argparse
import json

from gil.learn.curriculum import curriculum_for, curriculum_spec, robot_types


def main() -> None:
    ap = argparse.ArgumentParser(description="Print GIL robot types + staged learning curriculum as JSON.")
    ap.add_argument("--robot_type", default="", help="If set, print only this robot type's stages.")
    args = ap.parse_args()

    if args.robot_type:
        rt = str(args.robot_type).strip()
        if rt not in set(robot_types()):
            raise SystemExit(f"Unknown robot_type {rt!r}. Valid: {robot_types()}")
        stages = curriculum_for(rt)  # type: ignore[arg-type]
        payload = {"robot_type": rt, "stages": [s.__dict__ for s in stages]}
    else:
        payload = curriculum_spec()

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

