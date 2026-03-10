import asyncio
from dataclasses import dataclass

import carb
import omni.ext
import omni.kit.app
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom


@dataclass(frozen=True)
class TaskConfig:
    goal_x: float = 3.5
    goal_y: float = 3.5
    goal_z: float = 0.15
    goal_radius: float = 0.4
    print_every_sec: float = 1.0


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        carb.log_info(f"[gil.maze_task] startup ext_id={ext_id}")
        self._task = asyncio.ensure_future(self._run_when_ready())

    async def _run_when_ready(self) -> None:
        app = omni.kit.app.get_app()

        stage: Usd.Stage | None = None
        for _ in range(600):
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                break
            await app.next_update_async()
        if stage is None:
            carb.log_error("[gil.maze_task] No USD stage available after waiting")
            return

        cfg = self._read_cfg()
        goal_path = Sdf.Path("/World/Goal")

        # Create a visible goal marker (sphere)
        if stage.GetPrimAtPath(goal_path):
            stage.RemovePrim(goal_path)
        goal = UsdGeom.Sphere.Define(stage, goal_path)
        goal.CreateRadiusAttr(0.15)
        xform_goal = UsdGeom.Xformable(goal.GetPrim())
        op_t = xform_goal.AddTranslateOp()
        op_t.Set(Gf.Vec3d(cfg.goal_x, cfg.goal_y, cfg.goal_z))
        carb.log_info(f"[gil.maze_task] Goal spawned at ({cfg.goal_x:.2f},{cfg.goal_y:.2f}) r={cfg.goal_radius:.2f}")

        # Track progress to goal (no training yet; just metrics)
        t_last = 0.0
        prev_dist = None
        last_goal = (cfg.goal_x, cfg.goal_y, cfg.goal_z, cfg.goal_radius)
        while True:
            await app.next_update_async()
            # use wall-clock dt from Kit timeline isn't easily available here; just print periodically
            t_last += 1.0 / 60.0
            if t_last < cfg.print_every_sec:
                continue
            t_last = 0.0

            # Update goal marker if settings changed (e.g. via MCP set_humanoid_goal).
            cfg = self._read_cfg()
            cur_goal = (cfg.goal_x, cfg.goal_y, cfg.goal_z, cfg.goal_radius)
            if cur_goal != last_goal:
                last_goal = cur_goal
                try:
                    op_t.Set(Gf.Vec3d(cfg.goal_x, cfg.goal_y, cfg.goal_z))
                except Exception:
                    pass

            robot_pos = self._get_robot_xy(stage)
            if robot_pos is None:
                carb.log_warn("[gil.maze_task] Robot prim not found at /World/Humanoid (yet)")
                continue

            dx = robot_pos[0] - cfg.goal_x
            dy = robot_pos[1] - cfg.goal_y
            dist = (dx * dx + dy * dy) ** 0.5

            if prev_dist is None:
                delta = 0.0
            else:
                delta = prev_dist - dist
            prev_dist = dist

            reached = dist <= cfg.goal_radius
            carb.log_info(f"[gil.maze_task] dist_to_goal={dist:.3f} progress={delta:+.3f} reached={reached}")

    def _get_robot_xy(self, stage: Usd.Stage) -> tuple[float, float] | None:
        # Prefer pelvis link as base pose; many humanoid USDs keep the root Xform fixed.
        prim = stage.GetPrimAtPath("/World/Humanoid/pelvis")
        if not prim or not prim.IsValid():
            prim = stage.GetPrimAtPath("/World/Humanoid")
        if not prim or not prim.IsValid():
            return None
        xform = UsdGeom.Xformable(prim)
        m = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        p = m.ExtractTranslation()
        return float(p[0]), float(p[1])

    def _read_cfg(self) -> TaskConfig:
        s = carb.settings.get_settings()

        def gf(key: str, default: float) -> float:
            v = s.get(key)
            return default if v is None else float(v)

        return TaskConfig(
            goal_x=gf("/gil/task/goal_x", 3.5),
            goal_y=gf("/gil/task/goal_y", 3.5),
            goal_z=gf("/gil/task/goal_z", 0.15),
            goal_radius=gf("/gil/task/goal_radius", 0.4),
            print_every_sec=gf("/gil/task/print_every_sec", 1.0),
        )

    def on_shutdown(self) -> None:
        carb.log_info("[gil.maze_task] shutdown")


