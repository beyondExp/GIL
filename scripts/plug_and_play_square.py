from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from gil.core.robot_adapter import CmdVelCalibration, SafeEnvelope
from gil.orchestrator.action_compiler import ActionCompiler
from gil.orchestrator.mcp_humanoid_adapter import McpHumanoidAdapter
from gil.orchestrator.plug_and_play import PlugAndPlayBootstrap, CalibrationResult
from gil.orchestrator.skill_runner import HumanoidSkillRunner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_calibration(robot_id: str) -> CalibrationResult | None:
    p = _repo_root() / "profiles" / f"{robot_id}.calibration.json"
    if not p.is_file():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    cmd = raw.get("cmd_vel") or {}
    env = raw.get("envelope") or {}
    return CalibrationResult(
        observed_at_s=float(raw.get("observed_at_s") or 0.0),
        cmd_vel=CmdVelCalibration(
            vx_scale=float(cmd.get("vx_scale") or 1.0),
            vy_scale=float(cmd.get("vy_scale") or 1.0),
            wz_scale=float(cmd.get("wz_scale") or 1.0),
        ),
        envelope=SafeEnvelope(
            max_vx=float(env.get("max_vx") or 0.2),
            max_vy=float(env.get("max_vy") or 0.0),
            max_wz=float(env.get("max_wz") or 0.35),
            max_drive_duration_s=float(env.get("max_drive_duration_s") or 8.0),
            min_upright_base_z_m=float(env.get("min_upright_base_z_m") or 0.70),
        ),
        notes=list(raw.get("notes") or []),
    )


async def _amain() -> int:
    ap = argparse.ArgumentParser(description="Plug-and-play square skill (uses calibration + safe compiler).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_id", default="unitree_h1_sim")
    ap.add_argument("--bootstrap", action="store_true", help="If set, run calibration first.")
    ap.add_argument("--reset", action="store_true", help="If set, reset episode before walking.")
    ap.add_argument("--vx", type=float, default=0.20)
    ap.add_argument("--forward_s", type=float, default=8.0)
    ap.add_argument("--wz", type=float, default=0.25)
    ap.add_argument("--turn_s", type=float, default=1.0)
    ap.add_argument("--min_z", type=float, default=0.70)
    args = ap.parse_args()

    async with McpHumanoidAdapter(url=args.url, robot_id=str(args.robot_id)) as ad:
        cal = _load_calibration(str(args.robot_id))
        if cal is None or bool(args.bootstrap):
            boot = PlugAndPlayBootstrap(ad)
            cal = await boot.calibrate(min_z=float(args.min_z))
        compiler = ActionCompiler(envelope=cal.envelope, calib=cal.cmd_vel)
        runner = HumanoidSkillRunner(adapter=ad, compiler=compiler, envelope=cal.envelope)
        res = await runner.walk_square_cmd_vel(
            vx=float(args.vx),
            forward_s=float(args.forward_s),
            wz=float(args.wz),
            turn_s=float(args.turn_s),
            corners=4,
            reset=bool(args.reset),
        )
        print(json.dumps({"ok": res.ok, "skill": res.skill, "error": res.error, "metrics": res.metrics or {}, "calibration": cal.to_dict()}, indent=2))
        return 0 if res.ok else 2


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()

