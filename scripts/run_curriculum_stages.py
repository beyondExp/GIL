from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from gil.learn.curriculum import curriculum_for
from gil.agents.ollama_agent import OllamaInstructionAgent
from gil.memory.factory import make_spatial_memory
from gil.memory.types import BeliefState, Pose


@dataclass
class StageResult:
    stage_id: str
    ok: bool
    status: str  # pass|fail|skip
    reason: str = ""
    metrics: dict[str, Any] = None  # type: ignore[assignment]


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await session.call_tool(name, args)
    txt = res.content[0].text if res.content else "{}"
    try:
        out = json.loads(txt)
        return out if isinstance(out, dict) else {"value": out}
    except Exception:
        return {"_raw": txt}


def _finite(x: float) -> bool:
    return bool(math.isfinite(float(x)))


async def _get_base(session: ClientSession) -> dict[str, float]:
    st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
    b = st.get("base") or {}
    return {
        "x": float(b.get("x", 0.0)),
        "y": float(b.get("y", 0.0)),
        "z": float(b.get("z", 0.0)),
        "yaw": float(b.get("yaw", 0.0)),
        "vx": float(b.get("vx", 0.0)),
        "vy": float(b.get("vy", 0.0)),
        "wz": float(b.get("wz", 0.0)),
    }


async def _record_belief(mem, *, robot_id: str, base: dict[str, float], source: str, episode_id: str | None) -> None:
    pose = Pose(x=float(base["x"]), y=float(base["y"]), z=float(base["z"]), yaw=float(base["yaw"]), frame="world")
    belief = BeliefState(robot_id=robot_id, pose=pose, observed_at_s=time.time(), source=source)
    try:
        mem.record_belief(belief, episode_id=episode_id)
    except Exception:
        return


async def stage_safety_do_no_harm(session: ClientSession) -> StageResult:
    pre = await _call(session, "run_humanoid_preflight", {})
    ok = bool(pre.get("ok"))
    return StageResult(stage_id="safety_do_no_harm", ok=ok, status="pass" if ok else "fail", reason="" if ok else "preflight_failed", metrics={"preflight": pre})


async def stage_proprioception_calibration(session: ClientSession) -> StageResult:
    # Check we get finite base pose samples for a short window.
    good = 0
    for _ in range(15):
        b = await _get_base(session)
        if all(_finite(b[k]) for k in ("x", "y", "z", "yaw")) and b["z"] > 0.1:
            good += 1
        await asyncio.sleep(0.12)
    ok = good >= 6
    return StageResult(stage_id="proprioception_calibration", ok=ok, status="pass" if ok else "fail", reason="" if ok else "unstable_base_pose", metrics={"good_samples": good})


async def stage_stand_idle(session: ClientSession, *, min_z: float = 0.60, duration_s: float = 12.0) -> StageResult:
    await _call(session, "stop_humanoid_now", {"reason": "stand_idle"})
    await _call(session, "disable_humanoid_motion", {"reason": "stand_idle"})
    # Open-space stability: reset to a simple origin pose.
    await _call(session, "reset_humanoid_episode", {"x": 0.0, "y": 0.0, "yaw": 0.0})
    await _call(session, "enable_humanoid_motion", {"reason": "stand_idle"})
    t0 = time.time()
    min_seen = 9e9
    while (time.time() - t0) < duration_s:
        await _call(session, "send_humanoid_heartbeat", {"source": "stand_idle"})
        b = await _get_base(session)
        z = float(b["z"])
        min_seen = min(min_seen, z)
        if 0.0 < z < min_z:
            await _call(session, "disable_humanoid_motion", {"reason": "stand_idle_fail"})
            return StageResult(stage_id="stand_idle", ok=False, status="fail", reason="fell_or_low_z", metrics={"min_z": min_seen})
        await asyncio.sleep(0.5)
    await _call(session, "disable_humanoid_motion", {"reason": "stand_idle_done"})
    return StageResult(stage_id="stand_idle", ok=True, status="pass", metrics={"min_z": min_seen})


async def stage_locomotion_forward(session: ClientSession, *, vx: float = 0.10, duration_s: float = 6.0, min_z: float = 0.55) -> StageResult:
    await _call(session, "set_humanoid_mode", {"mode": "external"})
    await _call(session, "stop_humanoid_now", {"reason": "locomotion_forward"})
    await _call(session, "disable_humanoid_motion", {"reason": "locomotion_forward"})
    await _call(session, "reset_humanoid_episode", {"x": 0.0, "y": 0.0, "yaw": 0.0})
    await asyncio.sleep(0.8)
    await _call(session, "enable_humanoid_motion", {"reason": "locomotion_forward"})
    b0 = await _get_base(session)
    await _call(session, "send_humanoid_heartbeat", {"source": "locomotion_forward"})
    await _call(session, "drive_humanoid", {"vx": vx, "vy": 0.0, "wz": 0.0, "duration_s": duration_s, "reason": "forward"})
    b1 = await _get_base(session)
    await _call(session, "disable_humanoid_motion", {"reason": "locomotion_forward_done"})
    dx = float(b1["x"] - b0["x"])
    dy = float(b1["y"] - b0["y"])
    dist = float((dx * dx + dy * dy) ** 0.5)
    ok = (dist >= 0.12) and not (0.0 < float(b1["z"]) < min_z)
    return StageResult(
        stage_id="locomotion_forward",
        ok=ok,
        status="pass" if ok else "fail",
        reason="" if ok else "no_progress_or_fall",
        metrics={"dist_m": dist, "pose0": b0, "pose1": b1},
    )


async def stage_locomotion_turning(session: ClientSession, *, wz: float = 0.35, duration_s: float = 1.2, min_z: float = 0.55) -> StageResult:
    await _call(session, "set_humanoid_mode", {"mode": "external"})
    await _call(session, "stop_humanoid_now", {"reason": "locomotion_turning"})
    await _call(session, "disable_humanoid_motion", {"reason": "locomotion_turning"})
    await _call(session, "reset_humanoid_episode", {"x": 0.0, "y": 0.0, "yaw": 0.0})
    await asyncio.sleep(0.8)
    await _call(session, "enable_humanoid_motion", {"reason": "locomotion_turning"})
    b0 = await _get_base(session)
    await _call(session, "send_humanoid_heartbeat", {"source": "locomotion_turning"})
    await _call(session, "drive_humanoid", {"vx": 0.0, "vy": 0.0, "wz": wz, "duration_s": duration_s, "reason": "turn"})
    b1 = await _get_base(session)
    await _call(session, "disable_humanoid_motion", {"reason": "locomotion_turning_done"})
    dyaw = float(b1["yaw"] - b0["yaw"])
    ok = abs(dyaw) >= 0.6 and not (0.0 < float(b1["z"]) < min_z)
    return StageResult(
        stage_id="locomotion_turning",
        ok=ok,
        status="pass" if ok else "fail",
        reason="" if ok else "no_turn_or_fall",
        metrics={"dyaw": dyaw, "pose0": b0, "pose1": b1},
    )


async def stage_instruction_following_skills(session: ClientSession, *, instruction: str = "go forward one meter") -> StageResult:
    """
    Toy instruction stage: ask Ollama for a small local (dx,dy) goal and execute it.
    """
    try:
        agent = OllamaInstructionAgent()
    except Exception as e:
        return StageResult(stage_id="instruction_following_skills", ok=False, status="skip", reason=f"ollama_unavailable:{e}")

    await _call(session, "set_humanoid_mode", {"mode": "goal"})
    await _call(session, "stop_humanoid_now", {"reason": "instruction_following_skills"})
    await _call(session, "disable_humanoid_motion", {"reason": "instruction_following_skills"})
    await _call(session, "reset_humanoid_episode", {"x": 0.0, "y": 0.0, "yaw": 0.0})
    await asyncio.sleep(1.0)
    await _call(session, "enable_humanoid_motion", {"reason": "instruction_following_skills"})

    b0 = await _get_base(session)
    goal = agent.propose_goal_offset(instruction=instruction, context={"pose0": b0})
    dx = float(goal.get("dx", 0.0))
    dy = float(goal.get("dy", 0.0))
    # `set_humanoid_goal` expects absolute world x/y, so interpret (dx,dy) in the robot's local frame.
    yaw0 = float(b0.get("yaw", 0.0))
    cy = float(math.cos(yaw0))
    sy = float(math.sin(yaw0))
    gx = float(b0.get("x", 0.0)) + (cy * dx - sy * dy)
    gy = float(b0.get("y", 0.0)) + (sy * dx + cy * dy)
    res = await _call(session, "set_humanoid_goal", {"x": gx, "y": gy})
    if str(res.get("status") or "").lower() == "error":
        await _call(session, "disable_humanoid_motion", {"reason": "instruction_following_skills_goal_rejected"})
        return StageResult(
            stage_id="instruction_following_skills",
            ok=False,
            status="fail",
            reason="goal_rejected",
            metrics={"instruction": instruction, "goal_local": {"dx": dx, "dy": dy}, "goal_world": {"x": gx, "y": gy}, "set_goal": res},
        )

    t0 = time.time()
    while (time.time() - t0) < 12.0:
        await _call(session, "send_humanoid_heartbeat", {"source": "instruction_following_skills"})
        await asyncio.sleep(0.4)
    b1 = await _get_base(session)
    await _call(session, "disable_humanoid_motion", {"reason": "instruction_following_skills_done"})

    dist = float(((b1["x"] - b0["x"]) ** 2 + (b1["y"] - b0["y"]) ** 2) ** 0.5)
    ok = dist >= 0.35 and not (0.0 < float(b1["z"]) < 0.45)
    return StageResult(
        stage_id="instruction_following_skills",
        ok=ok,
        status="pass" if ok else "fail",
        reason="" if ok else "did_not_reach_local_goal",
        metrics={"instruction": instruction, "goal": {"dx": dx, "dy": dy}, "dist_m": dist, "pose0": b0, "pose1": b1},
    )


STAGE_IMPL = {
    "safety_do_no_harm": stage_safety_do_no_harm,
    "proprioception_calibration": stage_proprioception_calibration,
    "stand_idle": stage_stand_idle,
    "locomotion_forward": stage_locomotion_forward,
    "locomotion_turning": stage_locomotion_turning,
    "instruction_following_skills": stage_instruction_following_skills,
    # Others are intentionally skipped until implemented (balance recovery, get-up, nav, instruction following, etc.).
}


async def main() -> None:
    ap = argparse.ArgumentParser(description="Run curriculum stages and record to memory (Neo4j optional).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_type", default="humanoid_biped")
    ap.add_argument("--robot_id", default="humanoid")
    ap.add_argument("--limit", type=int, default=0, help="If >0, run only first N stages.")
    args = ap.parse_args()

    stages = curriculum_for(str(args.robot_type))  # type: ignore[arg-type]
    if int(args.limit) > 0:
        stages = stages[: int(args.limit)]

    mem = make_spatial_memory()
    try:
        mem.ensure_schema()
    except Exception:
        pass

    results: list[StageResult] = []
    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})

            for s in stages:
                sid = s.id
                started = time.time()
                episode_id = None
                try:
                    episode_id = mem.start_episode(robot_id=str(args.robot_id), stage_id=sid, started_at_s=started, meta={"curriculum": "v1"})
                except Exception:
                    episode_id = None

                impl = STAGE_IMPL.get(sid)
                if impl is None:
                    r = StageResult(stage_id=sid, ok=False, status="skip", reason="not_implemented")
                else:
                    try:
                        # periodic belief snapshot before stage execution
                        b = await _get_base(session)
                        await _record_belief(mem, robot_id=str(args.robot_id), base=b, source=f"stage:{sid}:pre", episode_id=episode_id)
                    except Exception:
                        pass
                    r = await impl(session)  # type: ignore[misc]
                    try:
                        b2 = await _get_base(session)
                        await _record_belief(mem, robot_id=str(args.robot_id), base=b2, source=f"stage:{sid}:post", episode_id=episode_id)
                    except Exception:
                        pass

                ended = time.time()
                try:
                    mem.end_episode(
                        episode_id=str(episode_id),
                        ended_at_s=ended,
                        outcome={"status": r.status, "ok": r.ok, "reason": r.reason, "metrics": r.metrics or {}},
                    )
                except Exception:
                    pass
                results.append(r)
                print(f"{sid}: {r.status} {('OK' if r.ok else '')} {r.reason}".strip(), flush=True)

    # Summary
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    print(json.dumps({"passed": passed, "failed": failed, "skipped": skipped, "results": [r.__dict__ for r in results]}, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=900.0))

