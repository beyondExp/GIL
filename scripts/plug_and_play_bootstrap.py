from __future__ import annotations

import argparse
import json
from pathlib import Path

from gil.orchestrator.mcp_humanoid_adapter import McpHumanoidAdapter
from gil.orchestrator.plug_and_play import PlugAndPlayBootstrap


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


async def _amain() -> int:
    ap = argparse.ArgumentParser(description="Plug-and-play bootstrap: calibrate cmd_vel semantics + emit a safe envelope.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--out", default="", help="Optional output path (defaults to profiles/<robot_id>.calibration.json).")
    ap.add_argument("--robot_id", default="humanoid")
    ap.add_argument("--min_z", type=float, default=0.70)
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else (_repo_root() / "profiles" / f"{args.robot_id}.calibration.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with McpHumanoidAdapter(url=args.url, robot_id=str(args.robot_id)) as ad:
        boot = PlugAndPlayBootstrap(ad)
        cal = await boot.calibrate(min_z=float(args.min_z))
        out_path.write_text(json.dumps(cal.to_dict(), indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "out": str(out_path), "calibration": cal.to_dict()}, indent=2))
        return 0


def main() -> None:
    import asyncio

    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()

