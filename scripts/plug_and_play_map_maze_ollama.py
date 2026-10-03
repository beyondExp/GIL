from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gil.agents.ollama_agent import OllamaDiscreteChoiceAgent
from gil.core.config import GilSettings
from gil.core.robot_adapter import CmdVelCalibration, SafeEnvelope
from gil.memory.factory import make_spatial_memory
from gil.orchestrator.action_compiler import ActionCompiler
from gil.orchestrator.mcp_humanoid_adapter import McpHumanoidAdapter
from gil.orchestrator.plug_and_play import CalibrationResult, PlugAndPlayBootstrap
from gil.world.cell_map import CellDiscoveryMap, cell_from_world, goal_cell
from gil.world.maze3d import generate_maze
from gil.world.path_spline import chaikin_smooth
from gil.world.mpc import MpcConfig, mpc_select_cmd
from gil.world.pure_pursuit import pick_lookahead_point, pure_pursuit_cmd


@dataclass(frozen=True)
class _Cmd:
    vx: float
    wz: float
    target: tuple[float, float]
    heading_err: float = 0.0


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


async def _wait_upright(ad: McpHumanoidAdapter, *, min_z: float, timeout_s: float = 15.0) -> bool:
    t0 = time.time()
    while (time.time() - t0) < float(timeout_s):
        st = await ad.get_state()
        b = st.base
        if b is not None and float(b.z) >= float(min_z):
            return True
        await asyncio.sleep(0.25)
    return False


async def _wait_pose_near(
    ad: McpHumanoidAdapter,
    *,
    target_xy: tuple[float, float],
    tol_m: float,
    min_z: float,
    stable_samples: int = 4,
    timeout_s: float = 15.0,
) -> bool:
    t0 = time.time()
    good = 0
    while (time.time() - t0) < float(timeout_s):
        st = await ad.get_state()
        b = st.base
        if b is None:
            good = 0
            await asyncio.sleep(0.15)
            continue
        ok = (
            abs(float(b.x) - float(target_xy[0])) <= float(tol_m)
            and abs(float(b.y) - float(target_xy[1])) <= float(tol_m)
            and float(b.z) >= float(min_z)
        )
        if ok:
            good += 1
            if good >= int(stable_samples):
                return True
        else:
            good = 0
        await asyncio.sleep(0.15)
    return False


def _wrap_pi(a: float) -> float:
    return float(math.atan2(math.sin(float(a)), math.cos(float(a))))


async def _detect_wz_sign(ad: McpHumanoidAdapter, compiler: ActionCompiler, *, wz_test: float = 0.10, dt_s: float = 0.65) -> float:
    """
    Probe the robot's yaw convention: send a small +wz turn and observe the sign of Δyaw.
    Returns +1.0 if +wz increases world yaw, else -1.0.
    """
    st0 = await ad.get_state()
    b0 = st0.base
    if b0 is None:
        return -1.0
    yaw0 = float(b0.yaw)
    a = compiler.cmd_vel(vx=0.0, vy=0.0, wz=float(wz_test), duration_s=float(dt_s), reason="wz_sign_probe")
    await ad.drive_cmd_vel(
        vx=float(a.payload["vx"]),
        vy=float(a.payload["vy"]),
        wz=float(a.payload["wz"]),
        duration_s=float(a.payload["duration_s"]),
        reason=str(a.payload.get("reason") or ""),
    )
    await asyncio.sleep(float(dt_s) + 0.15)
    st1 = await ad.get_state()
    b1 = st1.base
    if b1 is None:
        return -1.0
    dy = _wrap_pi(float(b1.yaw) - float(yaw0))
    return 1.0 if dy >= 0.0 else -1.0


async def _reset_to_spawn(
    ad: McpHumanoidAdapter,
    *,
    x: float,
    y: float,
    yaw: float,
    min_z: float,
    tol_m: float,
    require_cell: tuple[int, int] | None = None,
    maze=None,
) -> bool:
    # Best-effort robust reset: request and wait until the reported pose is near spawn.
    for _ in range(3):
        await ad.stop(reason="map_maze_reset")
        await ad.disable_motion(reason="map_maze_reset")
        try:
            await ad.reset_episode(x=float(x), y=float(y), yaw=float(yaw))
        except Exception:
            pass
        await ad.set_mode("external")
        # Reset pose can jitter for some robot variants / sensor rigs; accept once upright and near target.
        ok = await _wait_pose_near(
            ad,
            target_xy=(float(x), float(y)),
            tol_m=float(tol_m),
            min_z=float(min_z),
            stable_samples=5,
            timeout_s=18.0,
        )
        if ok:
            # Also require we landed in the intended spawn cell when a maze is provided. This prevents
            # accepting "nearby but wrong cell" resets (which breaks planning and causes immediate wall contact).
            if require_cell is not None and maze is not None:
                try:
                    st = await ad.get_state()
                    b = st.base
                    if b is None:
                        ok = False
                    else:
                        rc = cell_from_world(maze, x=float(b.x), y=float(b.y))
                        if (int(rc[0]), int(rc[1])) != (int(require_cell[0]), int(require_cell[1])):
                            ok = False
                except Exception:
                    ok = False
            if ok:
                return True
        await asyncio.sleep(0.4)
    return False


async def _heartbeat_loop(ad: McpHumanoidAdapter, *, interval_s: float, source: str) -> None:
    """Keep the safety watchdog satisfied even during expensive control steps."""
    while True:
        try:
            await ad.heartbeat(source=str(source))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(float(interval_s))


def _cells_to_world_polyline(maze, cells: list[tuple[int, int]]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for cx, cy in cells:
        pts.append(tuple(maze.cell_center(int(cx), int(cy))))
    return pts


def _wall_aabbs_2d(maze) -> list[tuple[float, float, float, float]]:
    aabbs: list[tuple[float, float, float, float]] = []
    for w in maze.walls:
        minx, miny, _minz, maxx, maxy, _maxz = w.aabb()
        aabbs.append((float(minx), float(miny), float(maxx), float(maxy)))
    return aabbs


def _hits_any_2d(aabbs: list[tuple[float, float, float, float]], *, x: float, y: float, r: float) -> bool:
    rr = float(r) * float(r)
    x0 = float(x)
    y0 = float(y)
    for minx, miny, maxx, maxy in aabbs:
        nx = min(max(x0, float(minx)), float(maxx))
        ny = min(max(y0, float(miny)), float(maxy))
        dx = x0 - nx
        dy = y0 - ny
        if dx * dx + dy * dy <= rr:
            return True
    return False


def _segment_clear_2d(
    aabbs: list[tuple[float, float, float, float]],
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    r: float,
    step_m: float = 0.06,
) -> bool:
    dx = float(x1) - float(x0)
    dy = float(y1) - float(y0)
    d = float(math.hypot(dx, dy))
    if d <= 1e-6:
        return not _hits_any_2d(aabbs, x=float(x0), y=float(y0), r=float(r))
    n = max(1, int(math.ceil(d / float(step_m))))
    for i in range(n + 1):
        t = float(i) / float(n)
        xi = float(x0) + t * dx
        yi = float(y0) + t * dy
        if _hits_any_2d(aabbs, x=float(xi), y=float(yi), r=float(r)):
            return False
    return True


def _doorway_waypoints(
    maze,
    *,
    cur: tuple[int, int],
    nxt: tuple[int, int],
    offset_m: float,
) -> list[tuple[float, float]]:
    """
    Return waypoints to cross from `cur` cell to adjacent `nxt` cell:
    approach point inside current cell near the opening,
    cross point just inside next cell,
    then next cell center.
    """
    cx, cy = int(cur[0]), int(cur[1])
    nx, ny = int(nxt[0]), int(nxt[1])
    cs = float(maze.spec.cell_size)
    ox = float(maze.spec.origin_x)
    oy = float(maze.spec.origin_y)
    # Door center on the shared boundary.
    if nx == cx + 1 and ny == cy:
        bx = ox + float(cx + 1) * cs
        by = oy + (float(cy) + 0.5) * cs
        return [(float(bx - offset_m), float(by)), (float(bx + offset_m), float(by)), tuple(maze.cell_center(nx, ny))]
    if nx == cx - 1 and ny == cy:
        bx = ox + float(cx) * cs
        by = oy + (float(cy) + 0.5) * cs
        return [(float(bx + offset_m), float(by)), (float(bx - offset_m), float(by)), tuple(maze.cell_center(nx, ny))]
    if ny == cy + 1 and nx == cx:
        bx = ox + (float(cx) + 0.5) * cs
        by = oy + float(cy + 1) * cs
        return [(float(bx), float(by - offset_m)), (float(bx), float(by + offset_m)), tuple(maze.cell_center(nx, ny))]
    if ny == cy - 1 and nx == cx:
        bx = ox + (float(cx) + 0.5) * cs
        by = oy + float(cy) * cs
        return [(float(bx), float(by + offset_m)), (float(bx), float(by - offset_m)), tuple(maze.cell_center(nx, ny))]
    # Non-adjacent; fallback.
    return [tuple(maze.cell_center(nx, ny))]


async def main() -> None:
    ap = argparse.ArgumentParser(description="Build a discovered map + LLM-chosen frontiers + spline tracking in a maze.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_id", default="unitree_h1_sim")
    ap.add_argument("--seed", type=int, default=0)

    ap.add_argument("--runtime_s", type=float, default=240.0)
    ap.add_argument("--tick_s", type=float, default=0.25)
    ap.add_argument("--reveal_radius_cells", type=int, default=1)
    ap.add_argument(
        "--connect_timeout_s",
        type=float,
        default=180.0,
        help="Seconds to wait for Isaac to connect to controls (preflight.ok).",
    )

    ap.add_argument("--min_z_start", type=float, default=0.65)
    ap.add_argument("--fall_z", type=float, default=0.55)
    ap.add_argument("--fall_persist_s", type=float, default=0.9)
    ap.add_argument("--goal_radius_m", type=float, default=None, help="Stop when within this radius of goal (defaults to maze spec).")
    ap.add_argument(
        "--fall_recovery",
        choices=["reset_spawn", "reset_in_place", "stop_only"],
        default="reset_spawn",
        help="What to do when a fall is detected.",
    )

    ap.add_argument("--vx", type=float, default=0.10)
    ap.add_argument("--wz_cap", type=float, default=0.24)
    ap.add_argument("--k_heading", type=float, default=2.0)
    ap.add_argument("--lookahead_m", type=float, default=0.65)
    ap.add_argument("--turn_in_place_err", type=float, default=0.95)
    ap.add_argument("--turn_min_vx_scale", type=float, default=0.12)
    ap.add_argument("--stall_s", type=float, default=8.0, help="If cell doesn't change for this long, bump turn authority.")
    ap.add_argument("--target_hold_s", type=float, default=4.0, help="Hold a chosen target for this long (reduces oscillation).")

    ap.add_argument("--smooth_iters", type=int, default=2)
    ap.add_argument("--controller", choices=["pursuit", "mpc", "waypoint"], default="waypoint")
    ap.add_argument("--mpc_rollouts", type=int, default=4000)
    ap.add_argument("--mpc_horizon_steps", type=int, default=10)
    ap.add_argument("--mpc_inflate_m", type=float, default=0.12)
    ap.add_argument("--waypoint_tol_m", type=float, default=0.22, help="Distance tolerance for considering a waypoint reached.")
    ap.add_argument(
        "--door_offset_frac",
        type=float,
        default=0.22,
        help="Doorway waypoint offset as fraction of cell_size (approach/cross points).",
    )
    ap.add_argument("--log_every", type=int, default=4, help="Log decision every N steps.")
    ap.add_argument("--use_full_maze_plan", action="store_true", help="Plan on full maze (skip frontier exploration).")
    ap.add_argument("--pull_latest_image_every_s", type=float, default=1.0, help="Fetch latest humanoid image via MCP for debugging/agent context.")
    ap.add_argument("--stuck_recovery", action="store_true", help="Enable backup+arc macro when stuck.")
    ap.add_argument("--wz_sign", type=float, default=-1.0, help="Command-space wz sign (+1 or -1) to match robot yaw convention.")
    ap.add_argument(
        "--auto_wz_sign",
        action="store_true",
        help="Probe yaw convention by sending a tiny +wz pulse and overriding --wz_sign accordingly.",
    )
    ap.add_argument("--use_ollama", action="store_true")
    ap.add_argument("--bootstrap", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--spawn_cell_x", type=int, default=1, help="Reset/spawn cell x (default: interior cell).")
    ap.add_argument("--spawn_cell_y", type=int, default=1, help="Reset/spawn cell y (default: interior cell).")
    ap.add_argument("--spawn_x", type=float, default=None, help="Reset/spawn x (overrides spawn_cell_x/y).")
    ap.add_argument("--spawn_y", type=float, default=None, help="Reset/spawn y (overrides spawn_cell_x/y).")
    ap.add_argument("--spawn_yaw", type=float, default=1.5707963267948966)
    ap.add_argument("--no_auto_spawn_yaw", action="store_true", help="Disable auto yaw alignment along first path segment.")
    ap.add_argument(
        "--settle_s",
        type=float,
        default=2.0,
        help="Seconds to wait after reset/upright before sending motion (lets physics/policy settle).",
    )

    ap.add_argument(
        "--recenter_if_near_wall",
        action="store_true",
        help="If the base drifts too close to a cell wall, reset to the current cell center (prevents wall-scrape loops).",
    )
    ap.add_argument(
        "--recenter_margin_m",
        type=float,
        default=0.14,
        help="Trigger recenter when distance to nearest cell wall is below this margin (meters).",
    )
    ap.add_argument(
        "--recenter_if_collided",
        action="store_true",
        help="If MPC reports collided=true for several consecutive steps, recenter to current cell center.",
    )
    ap.add_argument(
        "--recenter_collided_steps",
        type=int,
        default=6,
        help="How many consecutive collided=true steps trigger recenter (only when --recenter_if_collided is set).",
    )

    ap.add_argument("--write_map_every_s", type=float, default=2.0)
    args = ap.parse_args()

    # Planning model maze (must match Isaac /gil/maze/seed)
    maze = generate_maze()
    if int(args.seed) != int(maze.spec.seed):
        maze = generate_maze(type(maze.spec)(**{**maze.spec.__dict__, "seed": int(args.seed)}))  # type: ignore[arg-type]
    gcell = goal_cell(maze)
    # Default spawn: interior cell center (keeps robot away from boundary walls).
    if args.spawn_x is None or args.spawn_y is None:
        scx = max(0, min(int(maze.spec.width) - 1, int(args.spawn_cell_x)))
        scy = max(0, min(int(maze.spec.height) - 1, int(args.spawn_cell_y)))
        sx, sy = maze.cell_center(scx, scy)
        args.spawn_x = float(sx)
        args.spawn_y = float(sy)
    # Auto-align spawn yaw so the humanoid starts facing down the corridor, reducing immediate wall-scrape.
    if not bool(args.no_auto_spawn_yaw):
        try:
            path0 = maze.shortest_cell_path((float(args.spawn_x), float(args.spawn_y)), (float(maze.goal[0]), float(maze.goal[1])))
            if path0 and len(path0) >= 2:
                a = maze.cell_center(int(path0[0][0]), int(path0[0][1]))
                b = maze.cell_center(int(path0[1][0]), int(path0[1][1]))
                args.spawn_yaw = float(math.atan2(float(b[1]) - float(a[1]), float(b[0]) - float(a[0])))
        except Exception:
            pass

    goal_radius_m = float(maze.spec.goal_radius if args.goal_radius_m is None else float(args.goal_radius_m))
    goal_r2 = goal_radius_m * goal_radius_m

    agent: OllamaDiscreteChoiceAgent | None = None
    if bool(args.use_ollama):
        try:
            agent = OllamaDiscreteChoiceAgent()
        except Exception:
            agent = None

    # Memory backend (Neo4j optional)
    mem = make_spatial_memory(GilSettings())
    try:
        try:
            mem.ensure_schema()
        except Exception:
            pass
        episode_id = None
        try:
            episode_id = mem.start_episode(robot_id=str(args.robot_id), stage_id="maze_mapping_frontier", started_at_s=time.time(), meta={"seed": int(args.seed)})
        except Exception:
            episode_id = None

        async with McpHumanoidAdapter(url=str(args.url), robot_id=str(args.robot_id)) as ad:
            # Wait for sim client connectivity (Isaac can take a bit to attach after restarts).
            t_pf = time.time()
            pf = {"ok": False}
            while (time.time() - t_pf) < float(args.connect_timeout_s):
                try:
                    pf = await ad.preflight()
                except Exception:
                    pf = {"ok": False}
                if bool(pf.get("ok")):
                    break
                await asyncio.sleep(0.5)
            if not bool(pf.get("ok")):
                print(json.dumps({"ok": False, "error": "preflight_failed", "preflight": pf}, indent=2), flush=True)
                return

            # Optionally sync Isaac to a maze world seed (best-effort).
            try:
                await ad.call_tool("ingest_world", {"source": "text", "content": f"isaac maze seed {int(args.seed)}"})
            except Exception:
                pass

            cal = _load_calibration(str(args.robot_id))
            if cal is None or bool(args.bootstrap):
                boot = PlugAndPlayBootstrap(ad)
                cal = await boot.calibrate(min_z=float(args.min_z_start))

            compiler = ActionCompiler(envelope=cal.envelope, calib=CmdVelCalibration(vx_scale=1.0, vy_scale=1.0, wz_scale=1.0))

            # Reset + start in external mode (we do our own tracking controller).
            await ad.stop(reason="map_maze_setup")
            await ad.disable_motion(reason="map_maze_setup")
            if bool(args.reset) and ad.capabilities().supports_reset_episode:
                # Tight reset convergence: don't accept "nearby but wrong cell" resets.
                reset_tol = float(min(0.95, max(0.55, 0.55 * float(maze.spec.cell_size))))
                ok_reset = await _reset_to_spawn(
                    ad,
                    x=float(args.spawn_x),
                    y=float(args.spawn_y),
                    yaw=float(args.spawn_yaw),
                    min_z=float(args.min_z_start),
                    tol_m=float(reset_tol),
                    require_cell=(int(args.spawn_cell_x), int(args.spawn_cell_y)),
                    maze=maze,
                )
                if not ok_reset:
                    print(json.dumps({"ok": False, "error": "reset_failed_to_converge"}, indent=2))
                    return
            await ad.set_mode("external")
            ok_upright = await _wait_upright(ad, min_z=float(args.min_z_start), timeout_s=15.0)
            if not ok_upright:
                print(json.dumps({"ok": False, "error": "not_upright_at_start"}, indent=2))
                return
            # Give physics + policy a moment to stabilize after a reset before enabling motion.
            if float(args.settle_s) > 0:
                await asyncio.sleep(float(args.settle_s))
            en = await ad.enable_motion(reason="map_maze")
            if str(en.get("status") or "").lower() == "error":
                print(json.dumps({"ok": False, "error": "enable_failed", "enable": en}, indent=2))
                return

            # Determine command-space wz sign convention (needed for correct steering).
            wz_sign_cmd = -1.0 if float(args.wz_sign) < 0 else 1.0
            if bool(args.auto_wz_sign):
                try:
                    wz_sign_cmd = float(await _detect_wz_sign(ad, compiler))
                except Exception:
                    wz_sign_cmd = wz_sign_cmd
                print(
                    json.dumps(
                        {
                            "event": "wz_sign_detected",
                            "t_s": 0.0,
                            "wz_sign": float(wz_sign_cmd),
                        }
                    ),
                    flush=True,
                )

            disc = CellDiscoveryMap(width=maze.spec.width, height=maze.spec.height, seed=int(args.seed))
            low_z_since: float | None = None
            last_write_t = 0.0
            target_cell: tuple[int, int] | None = None
            target_set_t = 0.0
            last_cell = None
            last_cell_change_t = time.time()
            falls = 0
            recovered = 0
            outcome_ok = False
            outcome_status = "timeout"
            last_img_pull_t = 0.0
            has_image = False
            # Stuck recovery macro state
            recover_steps_left = 0
            recover_phase = "none"
            collided_streak = 0
            collided_recovery_dir = 1
            # Explicit waypoint queue for reliable doorway crossing (approach -> cross -> next center).
            wp_queue: list[tuple[float, float]] = []
            wp_for_edge: tuple[tuple[int, int], tuple[int, int]] | None = None
            # Cache 2D wall AABBs for clearance checks.
            wall_aabbs = _wall_aabbs_2d(maze)

            t0 = time.time()
            step = 0
            hb_task: asyncio.Task[None] | None = None
            try:
                # MPC can take > watchdog_timeout_s per step; keep heartbeating in the background so
                # motion doesn't get latched off mid-step.
                hb_task = asyncio.create_task(_heartbeat_loop(ad, interval_s=0.5, source="map_maze"))

                while (time.time() - t0) < float(args.runtime_s):
                    st = await ad.get_state()
                    b = st.base
                    if b is None:
                        await asyncio.sleep(float(args.tick_s))
                        continue

                    x, y, z, yaw = float(b.x), float(b.y), float(b.z), float(b.yaw)

                    # If we drift close to a wall within the current cell, recenter to the cell center.
                    # This avoids the "hugging the wall forever" failure mode that makes MPC report collided=true.
                    if bool(args.recenter_if_near_wall) and ad.capabilities().supports_reset_episode:
                        try:
                            rc0 = cell_from_world(maze, x=x, y=y)
                            cx0, cy0 = int(rc0[0]), int(rc0[1])
                            cs = float(maze.spec.cell_size)
                            wt = float(maze.spec.wall_thickness)
                            # Cell bounds (grid lines)
                            x0 = float(maze.spec.origin_x + cx0 * cs)
                            x1 = float(maze.spec.origin_x + (cx0 + 1) * cs)
                            y0 = float(maze.spec.origin_y + cy0 * cs)
                            y1 = float(maze.spec.origin_y + (cy0 + 1) * cs)
                            # Distance to *actual* walls only. Do NOT treat open passages as walls,
                            # otherwise we prevent crossing into the next cell.
                            dists: list[float] = []
                            # West wall at x0?
                            if cx0 >= 0 and bool(maze.v_walls[cy0][cx0]):
                                dists.append(float(x - (x0 + 0.5 * wt)))
                            # East wall at x1?
                            if (cx0 + 1) < int(maze.spec.width) and bool(maze.v_walls[cy0][cx0 + 1]):
                                dists.append(float((x1 - 0.5 * wt) - x))
                            # South wall at y0?
                            if cy0 >= 0 and bool(maze.h_walls[cy0][cx0]):
                                dists.append(float(y - (y0 + 0.5 * wt)))
                            # North wall at y1?
                            if (cy0 + 1) < int(maze.spec.height) and bool(maze.h_walls[cy0 + 1][cx0]):
                                dists.append(float((y1 - 0.5 * wt) - y))
                            d_wall = min(dists) if dists else 999.0
                            if float(d_wall) < float(args.recenter_margin_m):
                                ccx, ccy = maze.cell_center(cx0, cy0)
                                dist_to_center = float(math.hypot(float(x) - float(ccx), float(y) - float(ccy)))
                                # Guard: never recenter if it would teleport unusually far (indicates bad pose/cell).
                                if dist_to_center > (0.85 * float(maze.spec.cell_size)):
                                    print(
                                        json.dumps(
                                            {
                                                "event": "recenter_skip_far",
                                                "t_s": round(time.time() - t0, 2),
                                                "cell": [int(cx0), int(cy0)],
                                                "d_wall": round(float(d_wall), 3),
                                                "dist_to_center": round(float(dist_to_center), 3),
                                            }
                                        ),
                                        flush=True,
                                    )
                                else:
                                    print(
                                        json.dumps(
                                            {
                                                "event": "recenter_near_wall",
                                                "t_s": round(time.time() - t0, 2),
                                                "cell": [int(cx0), int(cy0)],
                                                "d_wall": round(float(d_wall), 3),
                                                "dist_to_center": round(float(dist_to_center), 3),
                                            }
                                        ),
                                        flush=True,
                                    )
                                ok_center = await _reset_to_spawn(
                                    ad,
                                    x=float(ccx),
                                    y=float(ccy),
                                    yaw=float(yaw),
                                    min_z=float(args.min_z_start),
                                    tol_m=float(min(0.60, max(0.35, 0.30 * float(maze.spec.cell_size)))),
                                    require_cell=(cx0, cy0),
                                    maze=maze,
                                )
                                if ok_center and float(args.settle_s) > 0:
                                    await asyncio.sleep(float(args.settle_s))
                                # Refresh pose after recenter before planning this tick.
                                st2 = await ad.get_state()
                                b2 = st2.base
                                if b2 is not None:
                                    x, y, z, yaw = float(b2.x), float(b2.y), float(b2.z), float(b2.yaw)
                        except Exception:
                            pass
                    # Pull latest image (so the agent has "what robot sees" available via MCP).
                    now_t = time.time()
                    if float(args.pull_latest_image_every_s) > 0 and (now_t - float(last_img_pull_t)) >= float(args.pull_latest_image_every_s):
                        last_img_pull_t = now_t
                        try:
                            img = await ad.call_tool("get_latest_image_for", {"robot_kind": "humanoid"})
                            has_image = bool((img.get("image") or img.get("image_left") or img.get("image_wide")))
                        except Exception:
                            has_image = False
                    d_goal2 = (x - float(maze.goal[0])) ** 2 + (y - float(maze.goal[1])) ** 2
                    if d_goal2 <= goal_r2 and z >= float(args.min_z_start):
                        outcome_ok = True
                        outcome_status = "reached_goal"
                        try:
                            await ad.stop(reason="map_maze_goal_reached")
                        except Exception:
                            pass
                        print(
                            json.dumps(
                                {
                                    "ok": True,
                                    "status": "reached_goal",
                                    "t_s": round(time.time() - t0, 2),
                                    "d_goal_m": round(float(math.sqrt(d_goal2)), 3),
                                    "falls": int(falls),
                                    "recovered": int(recovered),
                                },
                                indent=2,
                            ),
                            flush=True,
                        )
                        break

                    # fall persistence
                    if 0.0 < z < float(args.fall_z):
                        if low_z_since is None:
                            low_z_since = time.time()
                        if (time.time() - low_z_since) >= float(args.fall_persist_s):
                            falls += 1
                            print(
                                json.dumps(
                                    {
                                        "event": "fallen",
                                        "t_s": round(time.time() - t0, 2),
                                        "z": round(float(z), 3),
                                        "cell": [int(cell_from_world(maze, x=x, y=y)[0]), int(cell_from_world(maze, x=x, y=y)[1])],
                                        "recovery": str(args.fall_recovery),
                                    }
                                ),
                                flush=True,
                            )
                            await ad.stop(reason="map_maze_fall")
                            if str(args.fall_recovery) == "stop_only":
                                low_z_since = None
                                await asyncio.sleep(0.25)
                                continue
                            await ad.disable_motion(reason="map_maze_fall")
                            if ad.capabilities().supports_reset_episode:
                                if str(args.fall_recovery) == "reset_in_place":
                                    await ad.reset_episode(x=float(x), y=float(y), yaw=float(yaw))
                                else:
                                    await ad.reset_episode(x=float(args.spawn_x), y=float(args.spawn_y), yaw=float(args.spawn_yaw))
                            await ad.set_mode("external")
                            ok_up = await _wait_upright(ad, min_z=float(args.min_z_start), timeout_s=15.0)
                            if ok_up:
                                await ad.enable_motion(reason="map_maze_post_fall")
                                recovered += 1
                                if float(args.settle_s) > 0:
                                    await asyncio.sleep(float(args.settle_s))
                            else:
                                print(json.dumps({"ok": False, "status": "failed_to_recover_upright"}, indent=2), flush=True)
                            low_z_since = None
                            target_cell = None
                            await asyncio.sleep(0.6)
                            continue
                        await ad.stop(reason="map_maze_low_z_wait")
                        await asyncio.sleep(0.25)
                        continue
                    low_z_since = None

                    rc = cell_from_world(maze, x=x, y=y)
                    if last_cell is None or tuple(rc) != tuple(last_cell):
                        last_cell = tuple(rc)
                        last_cell_change_t = time.time()
                    disc.observed_at_s = float(time.time())
                    disc.reveal_local(maze, at=rc, radius_cells=int(args.reveal_radius_cells))

                    # persist the evolving map artifact (local file pointer, Neo4j optional)
                    now = time.time()
                    if (now - last_write_t) >= float(args.write_map_every_s):
                        last_write_t = now
                        out_dir = Path("gil_controls") / "logs" / "maps" / (episode_id or "no_episode")
                        out_path = out_dir / "cell_discovery_map.json"
                        disc.write_json(out_path, maze=maze, robot_cell=rc)
                        try:
                            mem.upsert_map_artifact(
                                robot_id=str(args.robot_id),
                                episode_id=(str(episode_id) if episode_id else None),
                                map_artifact={
                                    "map_id": str(episode_id or "no_episode") + "-cellmap",
                                    "kind": "cell_discovery_map_v1",
                                    "created_at_s": float(now),
                                    "frame": "world",
                                    "blob": {"uri": str(out_path.resolve()), "storage": "local_path"},
                                    "extra": {"seed": int(args.seed), "known_cells": len(disc.known_cells), "known_edges": len(disc.known_edges)},
                                },
                            )
                        except Exception:
                            pass

                    # Choose target: goal if discovered, else a frontier portal.
                    # IMPORTANT: hold a chosen target for a bit to avoid oscillation as portals reorder.
                    need_new_target = (
                        target_cell is None
                        or tuple(rc) == tuple(target_cell)
                        or (time.time() - float(target_set_t)) > float(args.target_hold_s)
                    )
                    if need_new_target:
                        if bool(args.use_full_maze_plan):
                            target_cell = gcell
                            target_set_t = time.time()
                        elif gcell in disc.known_cells:
                            target_cell = gcell
                            target_set_t = time.time()
                        else:
                            portals = disc.frontier_portals(maze)
                            if not portals:
                                target_cell = None
                            else:
                                options = []
                                for p in portals[:12]:
                                    fc = p.get("from_cell") or [0, 0]
                                    tc = p.get("to_cell") or [0, 0]
                                    options.append({"action": "go_frontier", "from_cell": fc, "to_cell": tc})
                                if agent is not None:
                                    ctx = {
                                        "robot_cell": [int(rc[0]), int(rc[1])],
                                        "goal_cell": [int(gcell[0]), int(gcell[1])],
                                        "known_cells": len(disc.known_cells),
                                        "frontiers": len(portals),
                                    }
                                    choice = agent.choose(task="pick next frontier to explore", options=options, context=ctx)
                                    picked = options[int(choice.get("choice", 0))]
                                else:
                                    picked = options[0]
                                # Drive to the known-side cell adjacent to the unknown portal.
                                fc = picked.get("from_cell") or [int(rc[0]), int(rc[1])]
                                target_cell = (int(fc[0]), int(fc[1]))
                                target_set_t = time.time()

                    if target_cell is None:
                        await ad.stop(reason="map_maze_no_target")
                        await asyncio.sleep(float(args.tick_s))
                        continue

                    # Plan: either full maze shortest path (best chance of success) or discovered graph.
                    if bool(args.use_full_maze_plan):
                        path_cells = maze.shortest_cell_path((x, y), (float(maze.goal[0]), float(maze.goal[1]))) or [rc]
                    else:
                        if target_cell not in disc.known_cells:
                            target_cell = rc
                        path_cells = disc.shortest_path_known(start=rc, goal=target_cell) or [rc]
                        if len(path_cells) < 2 and tuple(target_cell) != tuple(rc):
                            target_cell = None
                            await ad.stop(reason="map_maze_unreachable_target")
                            await asyncio.sleep(float(args.tick_s))
                            continue
                    # Build explicit doorway waypoints for the *next* cell transition.
                    next_cell: tuple[int, int] | None = None
                    if path_cells and tuple(path_cells[0]) == tuple(rc):
                        if len(path_cells) >= 2:
                            next_cell = (int(path_cells[1][0]), int(path_cells[1][1]))
                    elif path_cells:
                        # Defensive: path didn't start at current cell (pose/cell mismatch); fall back to current.
                        next_cell = None

                    # Refresh waypoint queue when we move to a new edge or when empty.
                    if next_cell is not None:
                        edge = (tuple(int(v) for v in rc), tuple(int(v) for v in next_cell))
                        if wp_for_edge != edge or (not wp_queue):
                            cs = float(maze.spec.cell_size)
                            wt = float(maze.spec.wall_thickness)
                            # Keep offsets well inside the cell, away from the wall thickness.
                            off = float(max(0.18 * cs, min(0.32 * cs, float(args.door_offset_frac) * cs)))
                            off = float(max(off, 0.5 * wt + 0.10))
                            cand = _doorway_waypoints(maze, cur=tuple(rc), nxt=tuple(next_cell), offset_m=float(off))
                            # Validate candidates: must be point-safe and segment-clear.
                            safe: list[tuple[float, float]] = []
                            # Use a conservative clearance radius: a bit smaller than maze radius helps when scraping.
                            r_safe = float(maze.spec.robot_radius) * 0.85
                            px0, py0 = float(x), float(y)
                            for (tx, ty) in cand:
                                if _hits_any_2d(wall_aabbs, x=float(tx), y=float(ty), r=float(r_safe)):
                                    continue
                                if not _segment_clear_2d(
                                    wall_aabbs,
                                    x0=float(px0),
                                    y0=float(py0),
                                    x1=float(tx),
                                    y1=float(ty),
                                    r=float(r_safe),
                                ):
                                    continue
                                safe.append((float(tx), float(ty)))
                                px0, py0 = float(tx), float(ty)
                            if not safe:
                                safe = [tuple(maze.cell_center(int(rc[0]), int(rc[1])))]
                            wp_queue = safe
                            wp_for_edge = edge
                    else:
                        wp_queue = [tuple(maze.cell_center(int(rc[0]), int(rc[1])))]
                        wp_for_edge = None

                    # Pop already-reached waypoints (await until actually reached before moving on).
                    while wp_queue:
                        d_wp = float(math.hypot(float(wp_queue[0][0]) - float(x), float(wp_queue[0][1]) - float(y)))
                        if d_wp <= float(args.waypoint_tol_m):
                            wp_queue.pop(0)
                            continue
                        break
                    if not wp_queue:
                        await ad.stop(reason="map_maze_wp_empty")
                        await asyncio.sleep(float(args.tick_s))
                        continue

                    exec_tgt = (float(wp_queue[0][0]), float(wp_queue[0][1]))

                    poly = [exec_tgt]
                    smooth = poly
                    ctrl = str(args.controller)
                    cmd: Any | None = None
                    mpc_meta: dict[str, Any] | None = None

                    # If stuck for a while, run a deterministic recovery macro (backup then arc)
                    stuck_for_s = time.time() - float(last_cell_change_t)
                    # Don't run recovery immediately on startup; give MPC time to make initial progress.
                    if bool(args.stuck_recovery) and int(step) >= 40 and stuck_for_s > float(args.stall_s):
                        if recover_steps_left <= 0:
                            recover_steps_left = 10
                            recover_phase = "backup"
                            print(
                                json.dumps(
                                    {
                                        "event": "stuck_recovery_start",
                                        "t_s": round(time.time() - t0, 2),
                                        "cell": [int(rc[0]), int(rc[1])],
                                        "stuck_for_s": round(float(stuck_for_s), 2),
                                    }
                                ),
                                flush=True,
                            )
                        # 6 steps backup, then 4 steps arc
                        if recover_steps_left > 4:
                            cmd = _Cmd(vx=-0.06, wz=0.0, target=(float(x), float(y)))
                            recover_phase = "backup"
                        else:
                            # Arc away: keep small forward + gentle turn
                            cmd = _Cmd(vx=0.06, wz=float(wz_sign_cmd) * 0.18, target=(float(x), float(y)))
                            recover_phase = "arc"
                        recover_steps_left -= 1
                        if recover_steps_left == 0:
                            # After macro, allow planner to try again.
                            last_cell_change_t = time.time()
                            print(json.dumps({"event": "stuck_recovery_done", "t_s": round(time.time() - t0, 2)}), flush=True)

                    # Rotate-then-go gating: for large heading errors, turn in place until aligned.
                    desired_yaw = float(math.atan2(float(exec_tgt[1]) - float(y), float(exec_tgt[0]) - float(x)))
                    err = _wrap_pi(float(desired_yaw) - float(yaw))
                    if abs(float(err)) > float(args.turn_in_place_err):
                        wz = float(max(-float(args.wz_cap), min(float(args.wz_cap), float(args.k_heading) * float(err)))) * float(wz_sign_cmd)
                        cmd = _Cmd(vx=0.0, wz=float(wz), target=exec_tgt, heading_err=float(err))
                        mpc_meta = {"mode": "turn_in_place", "heading_err": float(err)}

                    if cmd is None and ctrl == "waypoint":
                        # Simple closed-loop waypoint follower: small steering while moving forward.
                        wz = float(max(-float(args.wz_cap), min(float(args.wz_cap), float(args.k_heading) * float(err)))) * float(wz_sign_cmd)
                        # Reduce forward speed when slightly misaligned.
                        vx = float(args.vx)
                        if abs(float(err)) > 0.35:
                            vx = float(0.6 * float(args.vx))
                        cmd = _Cmd(vx=float(vx), wz=float(wz), target=exec_tgt, heading_err=float(err))

                    if cmd is None and ctrl == "mpc":
                        tgt = exec_tgt
                        # Stall recovery: if we haven't changed cells recently, temporarily allow stronger turning.
                        wz_cap_eff = float(args.wz_cap)
                        if (time.time() - float(last_cell_change_t)) > float(args.stall_s):
                            # Keep this modest for humanoids; large wz spikes tend to cause thrash/tipping.
                            wz_cap_eff = min(float(cal.envelope.max_wz), max(float(args.wz_cap), 0.18))
                        cfg = MpcConfig(
                            rollouts=int(args.mpc_rollouts),
                            horizon_steps=int(args.mpc_horizon_steps),
                            dt_s=float(args.tick_s),
                            base_vx=float(args.vx),
                            wz_cap=float(wz_cap_eff),
                            inflate_m=float(args.mpc_inflate_m),
                            wz_sign=float(wz_sign_cmd),
                        )
                        if cmd is None:
                            # MPC rollouts are CPU-heavy; run in a thread so the event loop can keep
                            # heartbeating and servicing MCP I/O while we compute the next action.
                            m = await asyncio.to_thread(
                                mpc_select_cmd,
                                maze=maze,
                                x=float(x),
                                y=float(y),
                                yaw=float(yaw),
                                target=tgt,
                                cfg=cfg,
                            )
                            mpc_meta = {
                                "score": float(m.score),
                                "collided": bool(m.collided),
                                "rollouts": int(cfg.rollouts),
                                "horizon_steps": int(cfg.horizon_steps),
                                "wz_cap_eff": float(wz_cap_eff),
                                "inflate_m": float(cfg.inflate_m),
                                "recovery_phase": (recover_phase if recover_phase != "none" else None),
                            }
                            cmd = _Cmd(vx=float(m.vx), wz=float(m.wz), target=(float(tgt[0]), float(tgt[1])))

                    if cmd is None and ctrl == "pursuit":
                        # Pursuit on a single waypoint is equivalent to simple goal-seek; keep for completeness.
                        cmd = pure_pursuit_cmd(
                            x=x,
                            y=y,
                            yaw=yaw,
                            path=[exec_tgt],
                            lookahead_m=float(args.lookahead_m),
                            vx=float(args.vx),
                            wz_cap=float(args.wz_cap),
                            k_heading=float(args.k_heading),
                            turn_in_place_err=float(args.turn_in_place_err),
                            turn_min_vx_scale=float(args.turn_min_vx_scale),
                        )
                        if cmd is None:
                            await ad.stop(reason="map_maze_no_cmd")
                            await asyncio.sleep(float(args.tick_s))
                            continue

                        # If MPC keeps saying "collided", run a short recovery macro to escape wall-scrape deadlocks.
                        if mpc_meta is not None and bool(mpc_meta.get("collided")):
                            collided_streak += 1
                        else:
                            collided_streak = 0
                        if (
                            bool(args.recenter_if_collided)
                            and int(args.recenter_collided_steps) > 0
                            and collided_streak >= int(args.recenter_collided_steps)
                        ):
                            try:
                                collided_recovery_dir = -1 if int(collided_recovery_dir) >= 0 else 1
                                print(
                                    json.dumps(
                                        {
                                            "event": "collided_recovery_macro",
                                            "t_s": round(time.time() - t0, 2),
                                            "cell": [int(rc[0]), int(rc[1])],
                                            "collided_streak": int(collided_streak),
                                            "dir": int(collided_recovery_dir),
                                        }
                                    ),
                                    flush=True,
                                )
                                # Recovery sequence: small back-off, turn-in-place, then a tiny forward pulse.
                                # This is intentionally non-teleporting (no reset) to avoid losing progress.
                                #
                                # Command-space wz: for MPC, cmd.wz is already in the robot's expected sign convention.
                                rec_wz = float(max(-float(args.wz_cap), min(float(args.wz_cap), float(args.wz_cap))))
                                # 1) Back up
                                a0 = compiler.cmd_vel(
                                    vx=-0.06,
                                    vy=0.0,
                                    wz=0.0,
                                    duration_s=min(0.9, float(args.tick_s)),
                                    reason="map_maze_collided_recovery_backup",
                                )
                                await ad.drive_cmd_vel(
                                    vx=float(a0.payload["vx"]),
                                    vy=float(a0.payload["vy"]),
                                    wz=float(a0.payload["wz"]),
                                    duration_s=float(a0.payload["duration_s"]),
                                    reason=str(a0.payload.get("reason") or ""),
                                )
                                await asyncio.sleep(float(a0.payload["duration_s"]))
                                # 2) Turn in place
                                a1 = compiler.cmd_vel(
                                    vx=0.0,
                                    vy=0.0,
                                    wz=float(collided_recovery_dir) * float(rec_wz),
                                    duration_s=min(0.9, float(args.tick_s)),
                                    reason="map_maze_collided_recovery_turn",
                                )
                                await ad.drive_cmd_vel(
                                    vx=float(a1.payload["vx"]),
                                    vy=float(a1.payload["vy"]),
                                    wz=float(a1.payload["wz"]),
                                    duration_s=float(a1.payload["duration_s"]),
                                    reason=str(a1.payload.get("reason") or ""),
                                )
                                await asyncio.sleep(float(a1.payload["duration_s"]))
                                # 3) Tiny forward pulse
                                a2 = compiler.cmd_vel(
                                    vx=min(0.08, float(args.vx)),
                                    vy=0.0,
                                    wz=0.0,
                                    duration_s=min(0.9, float(args.tick_s)),
                                    reason="map_maze_collided_recovery_forward",
                                )
                                await ad.drive_cmd_vel(
                                    vx=float(a2.payload["vx"]),
                                    vy=float(a2.payload["vy"]),
                                    wz=float(a2.payload["wz"]),
                                    duration_s=float(a2.payload["duration_s"]),
                                    reason=str(a2.payload.get("reason") or ""),
                                )
                                await asyncio.sleep(float(a2.payload["duration_s"]))
                                collided_streak = 0
                                await asyncio.sleep(float(args.tick_s))
                                continue
                            except Exception:
                                collided_streak = 0
                    else:
                        cmd = pure_pursuit_cmd(
                            x=x,
                            y=y,
                            yaw=yaw,
                            path=smooth,
                            lookahead_m=float(args.lookahead_m),
                            vx=float(args.vx),
                            wz_cap=float(args.wz_cap),
                            k_heading=float(args.k_heading),
                            turn_in_place_err=float(args.turn_in_place_err),
                            turn_min_vx_scale=float(args.turn_min_vx_scale),
                        )
                        if cmd is None:
                            await ad.stop(reason="map_maze_no_cmd")
                            await asyncio.sleep(float(args.tick_s))
                            continue

                    # For pursuit controller only: optionally bump turn authority if stalled.
                    if str(args.controller) == "pursuit":
                        wz_cap_eff = float(args.wz_cap)
                        if (time.time() - float(last_cell_change_t)) > float(args.stall_s):
                            wz_cap_eff = min(float(cal.envelope.max_wz), max(float(args.wz_cap), 0.35))
                        if wz_cap_eff != float(args.wz_cap):
                            cmd2 = pure_pursuit_cmd(
                                x=x,
                                y=y,
                                yaw=yaw,
                                path=smooth,
                                lookahead_m=float(args.lookahead_m),
                                vx=float(args.vx),
                                wz_cap=float(wz_cap_eff),
                                k_heading=float(args.k_heading),
                                turn_in_place_err=float(args.turn_in_place_err),
                                turn_min_vx_scale=float(args.turn_min_vx_scale),
                            )
                            if cmd2 is not None:
                                cmd = cmd2

                    # Convert controller-space wz into command-space expected by the robot.
                    wz_cmd = float(cmd.wz)
                    if str(args.controller) != "mpc":
                        wz_cmd = float(wz_sign_cmd) * float(cmd.wz)
                    act = compiler.cmd_vel(vx=float(cmd.vx), vy=0.0, wz=float(wz_cmd), duration_s=float(args.tick_s), reason="map_maze_track")
                    await ad.drive_cmd_vel(
                        vx=float(act.payload["vx"]),
                        vy=float(act.payload["vy"]),
                        wz=float(act.payload["wz"]),
                        duration_s=float(act.payload["duration_s"]),
                        reason=str(act.payload.get("reason") or ""),
                    )

                    if step % max(1, int(args.log_every)) == 0:
                        d_goal = float(math.sqrt((x - float(maze.goal[0])) ** 2 + (y - float(maze.goal[1])) ** 2))
                        print(
                            json.dumps(
                                {
                                    "t_s": round(time.time() - t0, 2),
                                    "controller": str(ctrl),
                                    "cell": [int(rc[0]), int(rc[1])],
                                    "target_cell": [int(target_cell[0]), int(target_cell[1])],
                                    "known_cells": len(disc.known_cells),
                                    "frontiers": len(disc.frontier_portals(maze)),
                                    "has_image": bool(has_image),
                                    "d_goal_m": round(d_goal, 2),
                                    "cmd": {"vx": round(float(cmd.vx), 3), "wz": round(float(cmd.wz), 3)},
                                    "target_xy": {"x": round(float(cmd.target[0]), 2), "y": round(float(cmd.target[1]), 2)},
                                    "wp_queue_len": int(len(wp_queue)),
                                    "mpc": mpc_meta,
                                }
                            ),
                            flush=True,
                        )
                    step += 1
                    # Let the cmd_vel pulse actually execute for its requested duration.
                    await asyncio.sleep(float(args.tick_s))
            finally:
                if hb_task is not None:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass

            # IMPORTANT: don't disable motion on normal exit; some humanoid controllers will go limp and fall.
            if not outcome_ok:
                try:
                    await ad.stop(reason="map_maze_timeout")
                except Exception:
                    pass
                print(
                    json.dumps(
                        {"ok": False, "status": str(outcome_status), "t_s": round(time.time() - t0, 2), "falls": int(falls), "recovered": int(recovered)},
                        indent=2,
                    ),
                    flush=True,
                )
    finally:
        try:
            if "episode_id" in locals() and locals().get("episode_id") is not None:
                outcome = {"ok": bool(locals().get("outcome_ok", False)), "status": str(locals().get("outcome_status", "done"))}
                mem.end_episode(episode_id=str(locals()["episode_id"]), ended_at_s=time.time(), outcome=outcome)
        except Exception:
            pass
        try:
            mem.close()
        except Exception:
            pass


if __name__ == "__main__":
    # Let --runtime_s control the run length; avoid a fixed global timeout cancelling MPC threads mid-step.
    asyncio.run(main())

