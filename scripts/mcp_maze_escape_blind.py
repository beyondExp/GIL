import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _wrap_pi(a: float) -> float:
    # wrap to (-pi, pi]
    return (a + math.pi) % (2 * math.pi) - math.pi


async def _get_base(session: ClientSession) -> dict:
    res = await session.call_tool("get_robot_state_for", {"robot_kind": "humanoid"})
    txt = res.content[0].text if res.content else ""
    data = json.loads(txt) if txt else {}
    return data.get("base") or {}


async def _drive(session: ClientSession, vx: float, vy: float, wz: float, duration_s: float, reason: str) -> None:
    await session.call_tool(
        "drive_humanoid",
        {"vx": float(vx), "vy": float(vy), "wz": float(wz), "duration_s": float(duration_s), "reason": str(reason)},
    )


async def _rotate_to(session: ClientSession, target_yaw: float, wz_left: float, max_time_s: float = 10.0) -> None:
    """Closed-loop rotate using odom yaw. Sends small wz pulses and re-reads yaw."""
    t0 = time.time()
    while time.time() - t0 < max_time_s:
        base = await _get_base(session)
        yaw = base.get("yaw")
        if yaw is None:
            return
        err = _wrap_pi(float(target_yaw) - float(yaw))
        if abs(err) < 0.20:
            return
        # In our current H1 policy setup, we observed: wz>0 makes yaw decrease (right turn),
        # so to increase yaw (turn left) we use wz_left < 0 and map proportional to -sign(err).
        wz = float(wz_left) if err > 0 else -float(wz_left)
        dur = min(0.35, max(0.10, abs(err) / 2.5))
        await _drive(session, vx=0.0, vy=0.0, wz=wz, duration_s=dur, reason=f"rotate_to yaw={target_yaw:.2f}")


async def _wait_for_upright(session: ClientSession, fall_z_m: float, timeout_s: float = 6.0) -> dict:
    """
    After a reset the reported base pose can be temporarily invalid/stale.
    Wait until base.z looks "upright" (>= fall_z_m) or until timeout.
    Returns the latest base dict.
    """
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_s:
        last = await _get_base(session)
        if last:
            try:
                z = float(last.get("z", 0.0))
            except Exception:
                z = 0.0
            if z >= fall_z_m:
                return last
        await asyncio.sleep(0.2)
    return last


@dataclass
class CellKey:
    ix: int
    iy: int


def _cell(x: float, y: float, cell_size_m: float) -> CellKey:
    return CellKey(int(round(x / cell_size_m)), int(round(y / cell_size_m)))


async def main() -> None:
    ap = argparse.ArgumentParser(description="Blind maze escape using odom-only wall-follow / exploration.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--runtime_s", type=float, default=120.0)
    ap.add_argument("--forward_vx", type=float, default=0.45)
    ap.add_argument("--forward_step_s", type=float, default=1.5)
    ap.add_argument("--stuck_dist_m", type=float, default=0.08, help="If we move less than this per step, consider stuck.")
    ap.add_argument("--cell_size_m", type=float, default=0.75)
    ap.add_argument("--wz_left", type=float, default=-1.0, help="Yaw rate for turning left (negative in current setup).")
    ap.add_argument("--nudge_wz", type=float, default=-0.9, help="Small turn applied when stuck (negative=left).")
    ap.add_argument("--fall_z_m", type=float, default=0.55, help="If base.z drops below this, consider fallen and reset.")
    ap.add_argument("--reset_settle_s", type=float, default=4.0, help="After reset, wait up to this long for base.z to stabilize.")
    ap.add_argument("--fall_grace_s", type=float, default=3.0, help="Ignore fall detection this long after a reset.")
    ap.add_argument("--backup_vx", type=float, default=-0.18, help="Backward speed used during unstuck.")
    ap.add_argument("--backup_s", type=float, default=0.6, help="Backup duration used during unstuck.")
    ap.add_argument("--reset", action="store_true", help="Reset episode before starting.")
    args = ap.parse_args()

    url = str(args.url)
    max_runtime_s = float(args.runtime_s)
    forward_vx = float(args.forward_vx)
    forward_step_s = float(args.forward_step_s)
    stuck_dist_m = float(args.stuck_dist_m)
    cell_size_m = float(args.cell_size_m)
    wz_left = float(args.wz_left)
    nudge_wz = float(args.nudge_wz)
    fall_z_m = float(args.fall_z_m)
    reset_settle_s = float(args.reset_settle_s)
    fall_grace_s = float(args.fall_grace_s)
    backup_vx = float(args.backup_vx)
    backup_s = float(args.backup_s)

    print(f"[escape] Connecting to {url}")
    async with streamablehttp_client(url, timeout=30, sse_read_timeout=30) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if args.reset:
                r = await session.call_tool("reset_humanoid_episode", {})
                if r.content:
                    print(r.content[0].text)
                last_reset_t = time.time()

            # Ensure external mode so cmd_vel-like driving is used
            await session.call_tool("set_humanoid_mode", {"mode": "external"})

            last_reset_t = time.time()
            base0 = await _wait_for_upright(session, fall_z_m=fall_z_m, timeout_s=reset_settle_s)
            if not base0:
                raise RuntimeError("No humanoid base state available. Is Isaac publishing odom into GIL Controls?")

            x0 = float(base0.get("x", 0.0))
            y0 = float(base0.get("y", 0.0))
            yaw0 = float(base0.get("yaw", 0.0))
            print(f"[escape] start: x={x0:.2f} y={y0:.2f} yaw={yaw0:.2f}")

            visits: dict[tuple[int, int], int] = {}
            stuck_count = 0

            t_start = time.time()
            step = 0
            while time.time() - t_start < max_runtime_s:
                step += 1
                base_before = await _get_base(session)
                if not base_before:
                    print("[escape] No base; stopping.")
                    return

                x = float(base_before.get("x", 0.0))
                y = float(base_before.get("y", 0.0))
                z = float(base_before.get("z", 0.0))
                yaw = float(base_before.get("yaw", 0.0))

                if z < fall_z_m and (time.time() - last_reset_t) >= fall_grace_s:
                    print(f"[escape] fallen detected (z={z:.2f} < {fall_z_m:.2f}); resetting episode")
                    r = await session.call_tool("reset_humanoid_episode", {})
                    if r.content:
                        print(r.content[0].text)
                    await session.call_tool("set_humanoid_mode", {"mode": "external"})
                    last_reset_t = time.time()
                    # refresh start reference after reset
                    base0 = await _wait_for_upright(session, fall_z_m=fall_z_m, timeout_s=reset_settle_s)
                    x0 = float(base0.get("x", 0.0))
                    y0 = float(base0.get("y", 0.0))
                    yaw0 = float(base0.get("yaw", 0.0))
                    print(f"[escape] re-start: x={x0:.2f} y={y0:.2f} yaw={yaw0:.2f}")
                    visits.clear()
                    stuck_count = 0
                    continue

                ck = _cell(x, y, cell_size_m)
                key = (ck.ix, ck.iy)
                visits[key] = visits.get(key, 0) + 1

                dist_from_start = math.hypot(x - x0, y - y0)
                if step % 5 == 0:
                    print(
                        f"[escape] step={step:03d} pos=({x:.2f},{y:.2f}) yaw={yaw:.2f} dist_from_start={dist_from_start:.2f} stuck={stuck_count}"
                    )

                # If we've wandered far enough, treat that as "maybe escaped" (you can tighten this later with a known exit pose).
                if dist_from_start > 12.0:
                    print(f"[escape] Reached dist_from_start={dist_from_start:.2f}m; stopping.")
                    return

                # If we've been stuck, pick a new direction that heads to a less-visited neighboring cell.
                if stuck_count > 0:
                    # Unstuck: back up a bit before choosing a new heading.
                    if stuck_count >= 3:
                        await _drive(session, vx=backup_vx, vy=0.0, wz=0.0, duration_s=backup_s, reason="unstuck_backup")
                    candidates = [0.0, math.pi / 2, -math.pi / 2, math.pi]  # relative
                    scored: list[tuple[int, float]] = []
                    for rel in candidates:
                        tyaw = _wrap_pi(yaw + rel)
                        px = x + math.cos(tyaw) * 1.0
                        py = y + math.sin(tyaw) * 1.0
                        pck = _cell(px, py, cell_size_m)
                        v = visits.get((pck.ix, pck.iy), 0)
                        scored.append((v, tyaw))
                    scored.sort(key=lambda t: (t[0], abs(_wrap_pi(t[1] - yaw))))
                    target_yaw = scored[0][1]
                    await _rotate_to(session, target_yaw, wz_left=wz_left, max_time_s=6.0)

                # Drive forward one step
                await _drive(session, vx=forward_vx, vy=0.0, wz=0.0, duration_s=forward_step_s, reason="blind_maze_escape_forward")

                base_after = await _get_base(session)
                if not base_after:
                    print("[escape] No base after drive; stopping.")
                    return

                x2 = float(base_after.get("x", 0.0))
                y2 = float(base_after.get("y", 0.0))
                moved = math.hypot(x2 - x, y2 - y)
                if moved < stuck_dist_m:
                    stuck_count = min(stuck_count + 1, 10)
                    # Nudge: small rotate to avoid infinite bumping
                    if stuck_count >= 2:
                        await _drive(session, vx=backup_vx, vy=0.0, wz=0.0, duration_s=backup_s, reason="unstuck_backup_small")
                    await _drive(session, vx=0.0, vy=0.0, wz=nudge_wz, duration_s=0.6, reason="unstuck_nudge_turn")
                else:
                    stuck_count = max(stuck_count - 1, 0)

            base_end = await _get_base(session)
            if base_end:
                print(f"[escape] timeout. end={json.dumps(base_end)}")
            else:
                print("[escape] timeout. end base unavailable.")


if __name__ == "__main__":
    asyncio.run(main())



