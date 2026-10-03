from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

from gil.agents.ollama_agent import OllamaDiscreteChoiceAgent
from gil.core.robot_adapter import CmdVelCalibration, SafeEnvelope
from gil.orchestrator.action_compiler import ActionCompiler
from gil.orchestrator.mcp_humanoid_adapter import McpHumanoidAdapter
from gil.orchestrator.plug_and_play import CalibrationResult, PlugAndPlayBootstrap
from gil.world.maze3d import generate_maze


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


def _dist2(ax: float, ay: float, bx: float, by: float) -> float:
    dx = ax - bx
    dy = ay - by
    return float(dx * dx + dy * dy)


def _wrap_pi(a: float) -> float:
    a = (a + math.pi) % (2 * math.pi) - math.pi
    return float(a)


async def _wait_for_reset_pose(
    ad: McpHumanoidAdapter,
    *,
    target_xy: tuple[float, float],
    tol_m: float,
    min_z: float,
    stable_samples: int = 4,
    timeout_s: float = 15.0,
) -> None:
    t0 = time.time()
    good = 0
    while (time.time() - t0) < timeout_s:
        st = await ad.get_state()
        b = st.base
        if b is None:
            good = 0
            await asyncio.sleep(0.15)
            continue
        if abs(float(b.x) - float(target_xy[0])) <= float(tol_m) and abs(float(b.y) - float(target_xy[1])) <= float(tol_m) and float(b.z) >= float(min_z):
            good += 1
            if good >= int(stable_samples):
                return
        else:
            good = 0
        await asyncio.sleep(0.15)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Maze escape: LLM chooses next waypoint, GIL executes safely.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_id", default="unitree_h1_sim")
    ap.add_argument("--runtime_s", type=float, default=240.0)
    ap.add_argument("--update_s", type=float, default=0.30)
    ap.add_argument("--goal_radius", type=float, default=0.65)
    ap.add_argument("--waypoint_radius", type=float, default=0.55)
    ap.add_argument("--lookahead_max", type=int, default=4, help="Max path indices to offer as options.")
    ap.add_argument("--min_z_start", type=float, default=0.65, help="Must be at/above this z to start moving.")
    ap.add_argument("--fall_z", type=float, default=0.55, help="Treat 0<z<fall_z as fallen if persistent.")
    ap.add_argument("--fall_persist_s", type=float, default=0.8, help="How long low-z must persist to count as a fall.")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--bootstrap", action="store_true", help="Run bootstrap calibration first.")
    ap.add_argument("--seed", type=int, default=0, help="Maze seed (must match Isaac maze_env seed).")
    ap.add_argument("--no_ollama", action="store_true", help="Disable LLM and pick deterministic waypoints.")
    ap.add_argument("--controller", choices=["external_pulse", "goal"], default="external_pulse")
    ap.add_argument("--stall_s", type=float, default=6.0, help="Seconds without meaningful motion before recovery menu appears.")
    ap.add_argument("--pulse_dt", type=float, default=0.25, help="Duration (s) for each cmd_vel pulse.")
    ap.add_argument("--vx", type=float, default=0.10, help="Forward speed for external pulses.")
    ap.add_argument("--wz_cap", type=float, default=0.22, help="Yaw rate cap for external pulses (rad/s).")
    ap.add_argument("--k_heading", type=float, default=2.0, help="Heading gain for external pulses.")
    ap.add_argument("--turn_in_place_err", type=float, default=0.95, help="If |heading err|>this, rotate before forward pulse.")
    ap.add_argument("--turn_min_vx_scale", type=float, default=0.12, help="When turning-in-place, keep vx = vx*scale to avoid spin slip.")
    ap.add_argument("--use_calibration", action="store_true", help="Apply stored cmd_vel scaling (off by default for maze).")
    ap.add_argument("--spawn_cell_x", type=int, default=1, help="Reset/spawn cell x (default: interior cell).")
    ap.add_argument("--spawn_cell_y", type=int, default=1, help="Reset/spawn cell y (default: interior cell).")
    ap.add_argument("--spawn_x", type=float, default=None, help="Reset/spawn x (overrides spawn_cell_x/y).")
    ap.add_argument("--spawn_y", type=float, default=None, help="Reset/spawn y (overrides spawn_cell_x/y).")
    ap.add_argument("--spawn_yaw", type=float, default=1.5707963267948966, help="Reset spawn yaw (rad).")
    ap.add_argument("--no_auto_spawn_yaw", action="store_true", help="Disable auto yaw alignment along first path segment.")
    args = ap.parse_args()

    maze = generate_maze()  # seed 0 by default in MazeSpec
    if int(args.seed) != int(maze.spec.seed):
        # Generate a matching maze model for planning (must match Isaac seed).
        maze = generate_maze(type(maze.spec)(**{**maze.spec.__dict__, "seed": int(args.seed)}))  # type: ignore[arg-type]
    if args.spawn_x is None or args.spawn_y is None:
        scx = max(0, min(int(maze.spec.width) - 1, int(args.spawn_cell_x)))
        scy = max(0, min(int(maze.spec.height) - 1, int(args.spawn_cell_y)))
        sx, sy = maze.cell_center(scx, scy)
        args.spawn_x = float(sx)
        args.spawn_y = float(sy)
    if not bool(args.no_auto_spawn_yaw):
        try:
            path0 = maze.shortest_cell_path((float(args.spawn_x), float(args.spawn_y)), (float(maze.goal[0]), float(maze.goal[1])))
            if path0 and len(path0) >= 2:
                a = maze.cell_center(int(path0[0][0]), int(path0[0][1]))
                b = maze.cell_center(int(path0[1][0]), int(path0[1][1]))
                args.spawn_yaw = float(math.atan2(float(b[1]) - float(a[1]), float(b[0]) - float(a[0])))
        except Exception:
            pass
    gx, gy = float(maze.goal[0]), float(maze.goal[1])
    goal_r2 = float(args.goal_radius) ** 2
    wp_r2 = float(args.waypoint_radius) ** 2
    # Maze bounds (with small padding) for safety resets.
    min_x = float(maze.spec.origin_x) - 0.25
    min_y = float(maze.spec.origin_y) - 0.25
    max_x = float(maze.spec.origin_x + maze.spec.width * maze.spec.cell_size) + 0.25
    max_y = float(maze.spec.origin_y + maze.spec.height * maze.spec.cell_size) + 0.25

    agent: OllamaDiscreteChoiceAgent | None = None
    if not bool(args.no_ollama):
        try:
            agent = OllamaDiscreteChoiceAgent()
        except Exception:
            agent = None

    async with McpHumanoidAdapter(url=str(args.url), robot_id=str(args.robot_id)) as ad:
        # Optionally sync the live Isaac maze seed (best-effort).
        try:
            await ad.call_tool("ingest_world", {"source": "text", "content": f"isaac maze seed {int(args.seed)}"})
        except Exception:
            pass

        cal = _load_calibration(str(args.robot_id))
        if cal is None or bool(args.bootstrap):
            boot = PlugAndPlayBootstrap(ad)
            cal = await boot.calibrate(min_z=float(args.min_z_start))
        # For maze driving, default to *not* applying calibration scaling unless explicitly requested.
        calib = cal.cmd_vel if bool(args.use_calibration) else CmdVelCalibration(vx_scale=1.0, vy_scale=1.0, wz_scale=1.0)
        compiler = ActionCompiler(envelope=cal.envelope, calib=calib)

        # Bring robot into a known safe start state.
        await ad.stop(reason="maze_llm_setup")
        await ad.disable_motion(reason="maze_llm_setup")
        if bool(args.reset) and ad.capabilities().supports_reset_episode:
            await ad.reset_episode(x=float(args.spawn_x), y=float(args.spawn_y), yaw=float(args.spawn_yaw))
            # Wait until the reported pose converges near spawn (avoid stale pose right after reset).
            await _wait_for_reset_pose(
                ad,
                target_xy=(float(args.spawn_x), float(args.spawn_y)),
                tol_m=0.65,
                min_z=float(args.min_z_start),
                timeout_s=15.0,
            )
        # Select controller mode (goal vs external pulses).
        await ad.set_mode("goal" if str(args.controller) == "goal" else "external")
        en = await ad.enable_motion(reason="maze_llm")
        if str(en.get("status") or "").lower() == "error":
            print(json.dumps({"ok": False, "error": "enable_failed", "enable": en, "calibration": cal.to_dict()}, indent=2))
            return

        last_set: tuple[float, float] | None = None
        last_goal_d: float | None = None
        last_progress_t = time.time()
        last_xy: tuple[float, float] | None = None
        last_move_t = time.time()
        low_z_since: float | None = None

        t0 = time.time()
        step = 0
        while (time.time() - t0) < float(args.runtime_s):
            await ad.heartbeat(source="maze_llm")
            st = await ad.get_state()
            b = st.base
            if b is None:
                await asyncio.sleep(float(args.update_s))
                continue

            x, y, z, yaw = float(b.x), float(b.y), float(b.z), float(b.yaw)

            # If we are out-of-bounds (common when maze walls are disabled), reset immediately.
            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                await ad.stop(reason="maze_llm_oob")
                await ad.disable_motion(reason="maze_llm_oob")
                if ad.capabilities().supports_reset_episode:
                    await ad.reset_episode(x=float(args.spawn_x), y=float(args.spawn_y), yaw=float(args.spawn_yaw))
                    await _wait_for_reset_pose(
                        ad,
                        target_xy=(float(args.spawn_x), float(args.spawn_y)),
                        tol_m=0.65,
                        min_z=float(args.min_z_start),
                        timeout_s=15.0,
                    )
                    await ad.set_mode("goal" if str(args.controller) == "goal" else "external")
                    await ad.enable_motion(reason="maze_llm_post_oob")
                    last_set = None
                    last_goal_d = None
                    last_progress_t = time.time()
                    last_xy = None
                    last_move_t = time.time()
                await asyncio.sleep(0.6)
                continue
            if last_xy is None:
                last_xy = (x, y)
                last_move_t = time.time()
            else:
                dxm = x - float(last_xy[0])
                dym = y - float(last_xy[1])
                if float(math.sqrt(dxm * dxm + dym * dym)) >= 0.03:
                    last_xy = (x, y)
                    last_move_t = time.time()
            # Fall detection with persistence (avoid transient dips / startup zeros).
            if 0.0 < z < float(args.fall_z):
                if low_z_since is None:
                    low_z_since = time.time()
                if (time.time() - low_z_since) >= float(args.fall_persist_s):
                    await ad.stop(reason="maze_llm_fall")
                    await ad.disable_motion(reason="maze_llm_fall")
                    if ad.capabilities().supports_reset_episode:
                        await ad.reset_episode(x=float(args.spawn_x), y=float(args.spawn_y), yaw=float(args.spawn_yaw))
                        await ad.set_mode("goal" if str(args.controller) == "goal" else "external")
                        await ad.enable_motion(reason="maze_llm_post_fall")
                        last_set = None
                        last_goal_d = None
                        last_progress_t = time.time()
                        last_xy = None
                        last_move_t = time.time()
                    low_z_since = None
                    await asyncio.sleep(0.6)
                else:
                    # Don’t “fight” while low-z; just stop and wait a beat.
                    await ad.stop(reason="maze_llm_low_z_wait")
                    await asyncio.sleep(0.25)
                continue
            low_z_since = None

            d_goal2 = _dist2(x, y, gx, gy)
            if d_goal2 <= goal_r2:
                await ad.stop(reason="maze_llm_goal_reached")
                await ad.disable_motion(reason="maze_llm_done")
                print(json.dumps({"ok": True, "status": "goal_reached", "t_s": time.time() - t0, "pose": b.__dict__}, indent=2))
                return

            d_goal = float(math.sqrt(d_goal2))
            if last_goal_d is None:
                last_goal_d = d_goal
            else:
                if d_goal < (last_goal_d - 0.05):
                    last_progress_t = time.time()
                last_goal_d = d_goal

            path = maze.shortest_cell_path((x, y), (gx, gy)) or []
            if len(path) < 2:
                candidates = [{"action": "reset", "label": "reset (no path)"}]
            else:
                max_i = max(1, min(int(args.lookahead_max), len(path) - 1))
                # For the external pulse controller, keep waypoints close to avoid diagonal wall fights.
                if str(args.controller) != "goal":
                    max_i = min(max_i, 2)
                # Offer a small menu that reduces corner-cutting:
                # - next cell
                # - farthest cell along the same corridor direction until a turn (or cap)
                # - farthest cell within cap
                candidates = []
                # corridor-forward index
                i_corr = 1
                try:
                    dx0 = int(path[1][0]) - int(path[0][0])
                    dy0 = int(path[1][1]) - int(path[0][1])
                    while (i_corr + 1) < len(path) and i_corr < max_i:
                        dx1 = int(path[i_corr + 1][0]) - int(path[i_corr][0])
                        dy1 = int(path[i_corr + 1][1]) - int(path[i_corr][1])
                        if (dx1, dy1) != (dx0, dy0):
                            break
                        i_corr += 1
                except Exception:
                    i_corr = 1

                def _add(i: int, label: str) -> None:
                    cx, cy = path[i]
                    tx, ty = maze.cell_center(int(cx), int(cy))
                    candidates.append(
                        {
                            "action": "goal",
                            "label": label,
                            "i": int(i),
                            "cell": [int(cx), int(cy)],
                            "goal": {"x": float(tx), "y": float(ty)},
                            "dist_to_waypoint_m": float(math.sqrt(_dist2(x, y, float(tx), float(ty)))),
                        }
                    )

                _add(1, "next_cell")
                if i_corr != 1:
                    _add(i_corr, "corridor_until_turn")
                if max_i not in (1, i_corr):
                    _add(max_i, "max_lookahead")

                stalled_s = float(time.time() - last_move_t)
                # Add recovery options when stalled.
                if stalled_s > float(args.stall_s):
                    if str(args.controller) == "goal":
                        candidates.insert(0, {"action": "external_pulse", "label": f"external_pulse (stalled_{stalled_s:.1f}s)"})
                    candidates.insert(0, {"action": "reset", "label": f"reset (stalled_{stalled_s:.1f}s)"})

            # Choose next action (LLM if available, else deterministic: corridor_until_turn > next_cell).
            default_choice = 0
            for j, c in enumerate(candidates):
                if str(c.get("action") or "") == "goal" and str(c.get("label") or "") == "corridor_until_turn":
                    default_choice = j
                    break
            choice = {
                "choice": int(default_choice),
                "action": str((candidates[default_choice].get("action") if candidates else "reset") or "goal"),
                "reason": "deterministic",
            }
            if agent is not None:
                try:
                    ctx = {
                        "robot": {"x": x, "y": y, "z": z, "yaw": yaw},
                        "goal": {"x": gx, "y": gy},
                        "d_goal_m": d_goal,
                        "maze_ascii": maze.render_ascii(x, y, gx=gx, gy=gy),
                        "last_waypoint": None if last_set is None else {"x": float(last_set[0]), "y": float(last_set[1])},
                        "stalled_s": float(time.time() - last_progress_t),
                    }
                    choice = agent.choose(task="escape the maze by picking the next waypoint or a safe reset", options=candidates, context=ctx)
                except Exception:
                    pass

            picked = candidates[int(choice["choice"])] if candidates else {"action": "reset"}
            action = str(choice.get("action") or picked.get("action") or "goal").strip().lower()
            if action == "reset" or str(picked.get("action") or "") == "reset":
                await ad.stop(reason="maze_llm_reset")
                await ad.disable_motion(reason="maze_llm_reset")
                if ad.capabilities().supports_reset_episode:
                    await ad.reset_episode(x=float(args.spawn_x), y=float(args.spawn_y), yaw=float(args.spawn_yaw))
                    await ad.set_mode("goal" if str(args.controller) == "goal" else "external")
                    await ad.enable_motion(reason="maze_llm_post_reset")
                last_set = None
                last_progress_t = time.time()
                last_xy = None
                last_move_t = time.time()
                await asyncio.sleep(0.6)
                continue

            tx = float((picked.get("goal") or {}).get("x", gx))
            ty = float((picked.get("goal") or {}).get("y", gy))

            # Execute toward the waypoint either in goal mode or via external pulses.
            if str(args.controller) == "goal":
                # If we're already at the waypoint, don't spam it.
                if _dist2(x, y, tx, ty) > 0.01 and (last_set is None or _dist2(float(last_set[0]), float(last_set[1]), tx, ty) > 0.01):
                    act = compiler.goal_xy(x=tx, y=ty, reason="maze_llm_waypoint")
                    res = await ad.set_goal_xy(x=float(act.payload["x"]), y=float(act.payload["y"]), reason=str(act.payload.get("reason") or ""))
                    last_set = (tx, ty)
                    if str(res.get("status") or "").lower() == "error":
                        await asyncio.sleep(0.3)
            else:
                # External pulse controller: rotate-then-go toward the waypoint.
                desired = float(math.atan2(float(ty) - y, float(tx) - x))
                err = _wrap_pi(desired - yaw)
                wz = float(max(-float(args.wz_cap), min(float(args.wz_cap), float(args.k_heading) * err)))
                if abs(err) > float(args.turn_in_place_err):
                    vx = float(args.vx) * float(max(0.0, min(1.0, float(args.turn_min_vx_scale))))
                else:
                    vx = float(args.vx)
                act = compiler.cmd_vel(vx=vx, vy=0.0, wz=wz, duration_s=float(args.pulse_dt), reason="maze_llm_pulse")
                res = await ad.drive_cmd_vel(
                    vx=float(act.payload["vx"]),
                    vy=float(act.payload["vy"]),
                    wz=float(act.payload["wz"]),
                    duration_s=float(act.payload["duration_s"]),
                    reason=str(act.payload.get("reason") or ""),
                )
                if str(res.get("status") or "").lower() == "error":
                    # If safety rejected motion, back off briefly and try again next tick.
                    await asyncio.sleep(0.4)

            if step % 10 == 0:
                rcx, rcy = maze.world_to_cell(x, y)
                print(
                    json.dumps(
                        {
                            "t_s": round(time.time() - t0, 2),
                            "pose": {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)},
                            "cell": [int(rcx), int(rcy)],
                            "d_goal_m": round(d_goal, 2),
                            "picked": picked,
                            "choice": choice,
                            "candidates_n": len(candidates),
                            "controller": str(args.controller),
                        }
                    ),
                    flush=True,
                )

            # If very close to waypoint, tick faster; else normal update.
            if _dist2(x, y, tx, ty) <= wp_r2:
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(float(args.update_s))
            step += 1

        # IMPORTANT: don't disable motion on normal exit; some humanoid controllers will go limp and fall.
        await ad.stop(reason="maze_llm_timeout")
        print(json.dumps({"ok": False, "status": "timeout", "t_s": time.time() - t0}, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=900.0))

