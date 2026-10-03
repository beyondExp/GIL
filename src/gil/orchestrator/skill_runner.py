from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import asyncio

from gil.core.robot_adapter import RobotAdapter, RobotState, SafeEnvelope
from gil.orchestrator.action_compiler import ActionCompiler, CompiledAction


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = float(b[0] - a[0])
    dy = float(b[1] - a[1])
    return float(math.sqrt(dx * dx + dy * dy))


@dataclass
class SkillResult:
    ok: bool
    skill: str
    error: str = ""
    metrics: dict[str, Any] | None = None


class HumanoidSkillRunner:
    """
    A small, robot-agnostic skill layer for humanoids:
    - uses RobotAdapter for IO
    - uses ActionCompiler for safe normalization/clamps

    This is what we want an "untrained agent" to call.
    """

    def __init__(self, *, adapter: RobotAdapter, compiler: ActionCompiler, envelope: SafeEnvelope):
        self.adapter = adapter
        self.compiler = compiler
        self.env = envelope

    async def _must_be_upright(self, st: RobotState) -> bool:
        if st.base is None:
            return False
        z = float(st.base.z)
        return not (0.0 < z < float(self.env.min_upright_base_z_m))

    async def _apply(self, act: CompiledAction) -> dict[str, Any]:
        if act.kind == "stop":
            return await self.adapter.stop(reason=str(act.payload.get("reason") or "stop"))
        if act.kind == "goal_xy":
            return await self.adapter.set_goal_xy(x=float(act.payload["x"]), y=float(act.payload["y"]), reason=str(act.payload.get("reason") or "goal"))
        if act.kind == "cmd_vel":
            return await self.adapter.drive_cmd_vel(
                vx=float(act.payload["vx"]),
                vy=float(act.payload["vy"]),
                wz=float(act.payload["wz"]),
                duration_s=float(act.payload["duration_s"]),
                reason=str(act.payload.get("reason") or ""),
                preview=bool(act.payload.get("preview", False)),
            )
        return {"status": "error", "error": f"unknown_action_kind:{act.kind}"}

    async def walk_square_cmd_vel(
        self,
        *,
        vx: float = 0.20,
        forward_s: float = 8.0,
        wz: float = 0.25,
        turn_s: float = 1.0,
        corners: int = 4,
        reset: bool = True,
    ) -> SkillResult:
        # Wait for the live backend to be ready.
        t0 = time.time()
        pre = {}
        while (time.time() - t0) < 15.0:
            pre = await self.adapter.preflight()
            if bool(pre.get("ok")):
                break
            await self.adapter.stop(reason="walk_square_wait_preflight")
            await self.adapter.disable_motion(reason="walk_square_wait_preflight")
            await asyncio.sleep(0.4)
        if not bool(pre.get("ok")):
            return SkillResult(ok=False, skill="walk_square_cmd_vel", error="preflight_failed", metrics={"preflight": pre})

        await self.adapter.stop(reason="walk_square_setup")
        await self.adapter.disable_motion(reason="walk_square_setup")
        if reset and self.adapter.capabilities().supports_reset_episode:
            try:
                await self.adapter.reset_episode()
            except Exception:
                pass
        if self.adapter.capabilities().supports_mode_switch:
            try:
                await self.adapter.set_mode("external")
            except Exception:
                pass
        en = await self.adapter.enable_motion(reason="walk_square")
        if str(en.get("status") or "").lower() == "error" or (not bool(en)):
            await self.adapter.stop(reason="walk_square_enable_failed")
            await self.adapter.disable_motion(reason="walk_square_enable_failed")
            return SkillResult(ok=False, skill="walk_square_cmd_vel", error="enable_motion_failed", metrics={"enable": en, "preflight": pre})

        st0 = await self.adapter.get_state()
        if not (await self._must_be_upright(st0)):
            await self.adapter.disable_motion(reason="walk_square_not_upright")
            return SkillResult(ok=False, skill="walk_square_cmd_vel", error="not_upright_at_start", metrics={"state": st0.__dict__})

        events: list[dict[str, Any]] = []
        total_dist = 0.0
        min_z_seen = float(st0.base.z if st0.base else 0.0)
        for i in range(int(corners)):
            st_a = await self.adapter.get_state()
            if st_a.base is None:
                await self.adapter.disable_motion(reason="walk_square_no_pose")
                return SkillResult(ok=False, skill="walk_square_cmd_vel", error="no_pose", metrics={"events": events})
            min_z_seen = min(min_z_seen, float(st_a.base.z))
            if not (await self._must_be_upright(st_a)):
                await self.adapter.stop(reason="walk_square_fallen")
                await self.adapter.disable_motion(reason="walk_square_fallen")
                return SkillResult(
                    ok=False,
                    skill="walk_square_cmd_vel",
                    error="fallen",
                    metrics={"events": events, "min_z_seen": min_z_seen, "pose": st_a.base.__dict__ if st_a.base else None},
                )

            # Forward
            act_fwd = self.compiler.cmd_vel(vx=vx, vy=0.0, wz=0.0, duration_s=forward_s, reason=f"square_forward_{i}")
            res_fwd = await self._apply(act_fwd)
            if str(res_fwd.get("status") or "").lower() == "error":
                await self.adapter.stop(reason="walk_square_motion_rejected_forward")
                await self.adapter.disable_motion(reason="walk_square_motion_rejected_forward")
                return SkillResult(
                    ok=False,
                    skill="walk_square_cmd_vel",
                    error="motion_rejected_forward",
                    metrics={"events": events, "result": res_fwd, "action": act_fwd.payload, "preflight": pre, "enable": en},
                )
            st_b = await self.adapter.get_state()
            if st_b.base is None:
                await self.adapter.disable_motion(reason="walk_square_no_pose_post_fwd")
                return SkillResult(ok=False, skill="walk_square_cmd_vel", error="no_pose_post_forward", metrics={"events": events})
            min_z_seen = min(min_z_seen, float(st_b.base.z))
            d = _dist((st_a.base.x, st_a.base.y), (st_b.base.x, st_b.base.y))
            total_dist += d
            events.append({"segment": f"forward_{i}", "action": act_fwd.payload, "result": res_fwd, "dist_m": d, "pose": st_b.base.__dict__})
            if not (await self._must_be_upright(st_b)):
                await self.adapter.stop(reason="walk_square_low_z_forward")
                await self.adapter.disable_motion(reason="walk_square_low_z_forward")
                return SkillResult(ok=False, skill="walk_square_cmd_vel", error="low_z_forward", metrics={"events": events, "min_z_seen": min_z_seen})

            # Turn pulse
            act_turn = self.compiler.cmd_vel(vx=0.0, vy=0.0, wz=wz, duration_s=turn_s, reason=f"square_turn_{i}")
            res_turn = await self._apply(act_turn)
            if str(res_turn.get("status") or "").lower() == "error":
                await self.adapter.stop(reason="walk_square_motion_rejected_turn")
                await self.adapter.disable_motion(reason="walk_square_motion_rejected_turn")
                return SkillResult(
                    ok=False,
                    skill="walk_square_cmd_vel",
                    error="motion_rejected_turn",
                    metrics={"events": events, "result": res_turn, "action": act_turn.payload, "preflight": pre, "enable": en},
                )
            st_c = await self.adapter.get_state()
            if st_c.base is None:
                await self.adapter.disable_motion(reason="walk_square_no_pose_post_turn")
                return SkillResult(ok=False, skill="walk_square_cmd_vel", error="no_pose_post_turn", metrics={"events": events})
            min_z_seen = min(min_z_seen, float(st_c.base.z))
            events.append({"segment": f"turn_{i}", "action": act_turn.payload, "result": res_turn, "pose": st_c.base.__dict__})
            if not (await self._must_be_upright(st_c)):
                await self.adapter.stop(reason="walk_square_low_z_turn")
                await self.adapter.disable_motion(reason="walk_square_low_z_turn")
                return SkillResult(ok=False, skill="walk_square_cmd_vel", error="low_z_turn", metrics={"events": events, "min_z_seen": min_z_seen})

        st1 = await self.adapter.get_state()
        await self.adapter.disable_motion(reason="walk_square_done")
        return SkillResult(
            ok=True,
            skill="walk_square_cmd_vel",
            metrics={
                "events": events,
                "total_dist_m": total_dist,
                "min_z_seen": min_z_seen,
                "pose0": st0.base.__dict__ if st0.base else None,
                "pose1": st1.base.__dict__ if st1.base else None,
                "observed_at_s": time.time(),
            },
        )

