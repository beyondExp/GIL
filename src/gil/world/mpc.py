from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from gil.world.maze3d import Maze3D
from gil.world.twin import Pose, UnicycleTwin, wrap_pi


Point2 = tuple[float, float]


@dataclass(frozen=True)
class MpcCmd:
    vx: float
    wz: float
    score: float
    collided: bool


@dataclass(frozen=True)
class MpcConfig:
    rollouts: int = 3000
    horizon_steps: int = 10
    dt_s: float = 0.25
    # action bounds / bias
    base_vx: float = 0.10
    wz_cap: float = 0.25
    # wall clearance shaping
    inflate_m: float = 0.10
    # Planning footprint scaling (helps when real robot can pass closer than our conservative radius).
    robot_radius_scale: float = 0.85
    # scoring weights
    w_progress: float = 2.0
    w_turn: float = 0.06
    # Penalizing speed can make the controller "freeze" in narrow corridors. Keep this at 0 by default
    # and let wall/turn penalties shape behavior instead.
    w_speed: float = 0.0
    w_near_wall: float = 0.65
    collision_penalty: float = 100.0
    # Convention switch: some controllers interpret +wz as clockwise (negative yaw in our world frame).
    # Set to -1.0 to model that and select the correct command sign.
    wz_sign: float = 1.0


def _capsule_hits_wall(maze: Maze3D, *, x: float, y: float, radius: float) -> bool:
    # Same logic as Maze3D.capsule_hits_wall, but with a custom radius (for "near wall" shaping).
    r = float(radius)
    z_lo, z_hi = 0.15, float(maze.spec.robot_height)
    for wall in maze.walls:
        minx, miny, minz, maxx, maxy, maxz = wall.aabb()
        if z_hi < minz or z_lo > maxz:
            continue
        nearest_x = min(max(float(x), float(minx)), float(maxx))
        nearest_y = min(max(float(y), float(miny)), float(maxy))
        if (float(x) - nearest_x) ** 2 + (float(y) - nearest_y) ** 2 <= r * r:
            return True
    return False


def _primitive_actions(*, base_vx: float, wz_cap: float) -> list[tuple[float, float]]:
    # Keep a small discrete action set but evaluate *sequences* of them (=> thousands of rollouts).
    vx = float(base_vx)
    wz = float(wz_cap)
    return [
        # Turn-in-place options (often much more stable for humanoids than arcing turns).
        (0.0, +0.6 * wz),
        (0.0, -0.6 * wz),
        (0.0, +wz),
        (0.0, -wz),
        (vx, 0.0),
        (vx, +0.6 * wz),
        (vx, -0.6 * wz),
        (0.6 * vx, +wz),
        (0.6 * vx, -wz),
        (0.35 * vx, +wz),
        (0.35 * vx, -wz),
        (-0.4 * vx, 0.0),  # backup to unstick from walls
    ]


def _score_rollout(
    *,
    start: Pose,
    end: Pose,
    target: Point2,
    d0: float,
    sum_abs_wz: float,
    sum_abs_vx: float,
    collided: bool,
    near_wall_hits: int,
    cfg: MpcConfig,
) -> float:
    df = math.hypot(float(target[0]) - float(end.x), float(target[1]) - float(end.y))
    progress = float(d0 - df)
    score = 0.0
    score += float(cfg.w_progress) * progress
    score -= float(cfg.w_turn) * float(sum_abs_wz)
    score -= float(cfg.w_speed) * float(sum_abs_vx)
    score -= float(cfg.w_near_wall) * float(near_wall_hits)
    if collided:
        score -= float(cfg.collision_penalty)
    return float(score)


def mpc_select_cmd(
    *,
    maze: Maze3D,
    x: float,
    y: float,
    yaw: float,
    target: Point2,
    cfg: MpcConfig,
) -> MpcCmd:
    """
    Sample many short-horizon rollouts in the kinematic twin and return the *first* cmd_vel pulse.

    This is the "simulate thousands before moving" controller. It does NOT require Isaac to step.
    Isaac only receives the chosen pulse after selection.
    """
    # Local import to avoid hard dependency in non-MPC paths.
    import random

    rng = random.Random(0)  # deterministic; the outer loop re-plans every tick anyway
    primitives = _primitive_actions(base_vx=float(cfg.base_vx), wz_cap=float(cfg.wz_cap))
    if not primitives:
        return MpcCmd(vx=0.0, wz=0.0, score=-1e9, collided=False)

    # Precompute 2D wall AABBs (z is irrelevant for ground-plane capsule checks here).
    aabbs: list[tuple[float, float, float, float]] = []
    for w in maze.walls:
        minx, miny, _, maxx, maxy, _ = w.aabb()
        aabbs.append((float(minx), float(miny), float(maxx), float(maxy)))

    def hits_any(px: float, py: float, r: float) -> bool:
        rr = float(r) * float(r)
        x0 = float(px)
        y0 = float(py)
        for minx, miny, maxx, maxy in aabbs:
            nx = min(max(x0, minx), maxx)
            ny = min(max(y0, miny), maxy)
            dx = x0 - nx
            dy = y0 - ny
            if dx * dx + dy * dy <= rr:
                return True
        return False

    start_pose = Pose(float(x), float(y), float(yaw))
    d0 = math.hypot(float(target[0]) - float(x), float(target[1]) - float(y))

    best_first = (0.0, 0.0)
    best_score = -1e18
    best_collided = False

    # Precompute radii for collision + near-wall shaping.
    r0 = float(maze.spec.robot_radius) * float(max(0.3, min(1.0, float(cfg.robot_radius_scale))))
    r_infl = float(r0 + max(0.0, float(cfg.inflate_m)))
    # If we're already scraping a wall (common with humanoids in narrow cells), the conservative radius can make
    # *every* action look like an immediate collision (even turn-in-place). Allow a smaller effective radius so the
    # controller can pick an escape action (typically a back-off + turn).
    r_col = float(r0)
    r_near = float(r_infl)
    try:
        if hits_any(float(start_pose.x), float(start_pose.y), float(r_col)):
            r_col = float(max(0.30 * float(r0), 0.65 * float(r0)))
            r_near = float(r_col + max(0.0, float(cfg.inflate_m)))
    except Exception:
        r_col = float(r0)
        r_near = float(r_infl)

    for _ in range(int(cfg.rollouts)):
        # Rollout state as raw floats (avoid object churn).
        px = float(start_pose.x)
        py = float(start_pose.y)
        pyaw = float(start_pose.yaw)
        collided = False
        near_wall_hits = 0
        sum_abs_wz = 0.0
        sum_abs_vx = 0.0
        first = None

        for t in range(int(cfg.horizon_steps)):
            vx, wz = primitives[rng.randrange(0, len(primitives))]
            # mild heading bias toward target by occasionally swapping to a corrective primitive
            if t < 3:
                desired = math.atan2(float(target[1]) - float(py), float(target[0]) - float(px))
                err = wrap_pi(desired - float(pyaw))
                if abs(err) > 0.5:
                    # Convert "desired yaw error" into command-space wz respecting cfg.wz_sign.
                    wz = float(max(-float(cfg.wz_cap), min(float(cfg.wz_cap), 1.6 * err))) * float(cfg.wz_sign)
                    # Prefer turn-in-place when the heading error is large; it reduces wall strikes and tipping.
                    # Use a fairly low threshold since humanoids tend to wall-scrape during arcing turns in tight cells.
                    if abs(float(err)) > 0.70:
                        vx = 0.0
                    else:
                        vx = float(max(0.25 * float(cfg.base_vx), min(float(cfg.base_vx), abs(vx))))

            if first is None:
                first = (float(vx), float(wz))

            # Integrate forward with conservative collision checks at sub-steps.
            dt = float(cfg.dt_s)
            dist = abs(float(vx)) * dt
            n = max(1, int(math.ceil(dist / 0.08)))
            sdt = dt / n
            sx, sy, syaw = px, py, pyaw
            ok = True
            for _i in range(n):
                c = math.cos(pyaw)
                s = math.sin(pyaw)
                px = px + float(vx) * c * sdt
                py = py + float(vx) * s * sdt
                # Apply command-space wz with configured yaw convention.
                pyaw = wrap_pi(pyaw + float(cfg.wz_sign) * float(wz) * sdt)
                if hits_any(px, py, r_col):
                    ok = False
                    break
                if hits_any(px, py, r_near):
                    near_wall_hits += 1
            if not ok:
                px, py, pyaw = sx, sy, syaw
                collided = True
                break

            sum_abs_wz += abs(float(wz)) * float(cfg.dt_s)
            sum_abs_vx += abs(float(vx)) * float(cfg.dt_s)

        score = _score_rollout(
            start=start_pose,
            end=Pose(px, py, pyaw),
            target=target,
            d0=float(d0),
            sum_abs_wz=float(sum_abs_wz),
            sum_abs_vx=float(sum_abs_vx),
            collided=bool(collided),
            near_wall_hits=int(near_wall_hits),
            cfg=cfg,
        )
        if score > best_score and first is not None:
            best_score = float(score)
            best_first = (float(first[0]), float(first[1]))
            best_collided = bool(collided)

    return MpcCmd(vx=float(best_first[0]), wz=float(best_first[1]), score=float(best_score), collided=bool(best_collided))

