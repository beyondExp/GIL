#!/usr/bin/env python3
"""Live Isaac maze escape using REST endpoints (no MCP streaming).

Why: The StreamableHTTP MCP client can be sensitive to transient connection drops on Windows.
This script drives the same Isaac-side goal walker via `gil_controls` REST endpoints, which
keeps the run robust and easy to inspect while the Isaac Sim GUI is open.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request

from gil.world.maze3d import MazeSpec, generate_maze


BASE = "http://127.0.0.1:6769"

# Maze must match Isaac launch defaults (run_isaac_h1_maze_real.ps1)
MAZE_SPEC = MazeSpec(width=9, height=9, seed=0, cell_size=1.4)

# Waypoint tracking
WAYPOINT_RADIUS_M = 0.65
WAYPOINT_TIMEOUT_S = 120.0
HEARTBEAT_HZ = 5.0
MIN_UPRIGHT_Z_M = 0.45
MAX_RUN_CELLS = 1


def _get_json(url: str, timeout_s: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url: str, payload: dict | None = None, timeout_s: float = 8.0) -> dict:
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        raw = r.read().decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {"ok": True, "raw": raw}


def _base_pose() -> tuple[float, float, float]:
    snap = _get_json(f"{BASE}/api/inspector", timeout_s=3.0)
    base = (((snap.get("observation") or {}).get("state") or {}).get("base") or {})
    return float(base.get("x", 0.0) or 0.0), float(base.get("y", 0.0) or 0.0), float(base.get("z", 0.0) or 0.0)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


def main() -> int:
    maze = generate_maze(MAZE_SPEC)
    goal_xy = (float(maze.goal[0]), float(maze.goal[1]))

    print("Resetting episode...")
    _post_json(f"{BASE}/api/humanoid/mode", {"mode": "external"})
    _post_json(f"{BASE}/api/humanoid/motion/disable", {"reason": "reset"})
    _post_json(f"{BASE}/api/reset", {})
    time.sleep(1.0)

    x0, y0, z0 = _base_pose()
    scx, scy = maze.world_to_cell(float(x0), float(y0))
    start_xy = maze.cell_center(int(scx), int(scy))
    d0 = _dist(start_xy, goal_xy)
    print(f"Start: ({x0:+.2f}, {y0:+.2f}, z={z0:+.2f}) dist_to_exit={d0:.2f}m")
    print(f"Exit:  ({goal_xy[0]:+.2f}, {goal_xy[1]:+.2f})")

    path = maze.shortest_cell_path(start_xy, goal_xy)
    if not path:
        print("FAIL: no cell path to exit")
        return 1

    cells = list(path)
    compressed: list[tuple[int, int]] = []
    if len(cells) >= 2:
        i = 0
        while i < len(cells) - 1:
            dx = int(cells[i + 1][0] - cells[i][0])
            dy = int(cells[i + 1][1] - cells[i][1])
            run_dir = (dx, dy)
            j = i + 1
            steps = 1
            while j < len(cells) - 1 and steps < MAX_RUN_CELLS:
                ndx = int(cells[j + 1][0] - cells[j][0])
                ndy = int(cells[j + 1][1] - cells[j][1])
                if (ndx, ndy) != run_dir:
                    break
                j += 1
                steps += 1
            compressed.append(cells[j])
            i = j
    waypoints = [maze.cell_center(cx, cy) for (cx, cy) in compressed]
    print(f"Path: {len(path)} cells -> {len(waypoints)} safe-waypoints (max_run={MAX_RUN_CELLS})")

    _post_json(f"{BASE}/api/humanoid/mode", {"mode": "goal"})
    en = _post_json(f"{BASE}/api/humanoid/motion/enable", {"reason": "maze_escape_waypoints_rest"})
    if str(en.get("status") or "").lower() != "success":
        print(f"FAIL: enable_motion: {en}")
        return 1

    t0 = time.time()
    for i, (wx, wy) in enumerate(waypoints):
        _post_json(f"{BASE}/api/humanoid/goal", {"x": float(wx), "y": float(wy)})
        print(f"\nWP {i+1:02d}/{len(waypoints)}: goal=({wx:+.2f},{wy:+.2f})")

        t_wp = time.time()
        within = 0
        away = 0
        last_d = None
        while True:
            x, y, z = _base_pose()
            if z != 0.0 and z < MIN_UPRIGHT_Z_M:
                print(f"FAIL: fell (z={z:.2f} < {MIN_UPRIGHT_Z_M:.2f}); stopping")
                _post_json(f"{BASE}/api/humanoid/motion/disable", {"reason": "fall_detected"})
                return 1
            d_wp = _dist((x, y), (wx, wy))
            d_exit = _dist((x, y), goal_xy)
            if last_d is None or (time.time() - t_wp) < 1.0 or int((time.time() - t_wp) * 10) % 10 == 0:
                print(f"  pos=({x:+.2f},{y:+.2f}) z={z:+.2f} d_wp={d_wp:.2f} d_exit={d_exit:.2f}")

            if d_wp <= WAYPOINT_RADIUS_M:
                within += 1
                if within >= 3:
                    break
            else:
                within = 0

            if (time.time() - t_wp) > WAYPOINT_TIMEOUT_S:
                print("FAIL: waypoint timeout (stalled or blocked)")
                _post_json(f"{BASE}/api/humanoid/motion/disable", {"reason": "waypoint_timeout"})
                return 1

            if last_d is not None:
                if d_wp > last_d + 0.25:
                    away += 1
                else:
                    away = max(0, away - 1)
                if away >= 12 and (time.time() - t_wp) > 8.0:
                    print("FAIL: moving away from waypoint (likely wrong goal or stuck)")
                    _post_json(f"{BASE}/api/humanoid/motion/disable", {"reason": "moving_away"})
                    return 1

            last_d = d_wp
            time.sleep(1.0 / HEARTBEAT_HZ)

    _post_json(f"{BASE}/api/humanoid/motion/disable", {"reason": "done"})
    xf, yf, zf = _base_pose()
    df = _dist((xf, yf), goal_xy)
    elapsed = time.time() - t0

    goal_cell = maze.world_to_cell(float(goal_xy[0]), float(goal_xy[1]))
    cur_cell = maze.world_to_cell(float(xf), float(yf))
    if df <= float(maze.spec.goal_radius) or (cur_cell == goal_cell):
        print(f"\nPASS: reached exit in {elapsed:.0f}s (dist={df:.2f}m cell={cur_cell})")
        return 0
    print(f"\nDONE: waypoints complete but still dist_to_exit={df:.2f}m (elapsed {elapsed:.0f}s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

