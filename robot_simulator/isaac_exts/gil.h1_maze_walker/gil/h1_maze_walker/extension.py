import asyncio
import json
import math
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Any

import carb
import numpy as np
import omni.ext
import omni.kit.app
import omni.timeline
import omni.usd
import websockets
from isaacsim.core.api import World
from isaacsim.core.utils.rotations import quat_to_euler_angles
try:
    # Built-in Isaac Sim locomotion policy example for Unitree H1.
    # We optionally try to reuse it for H1-2 (best-effort).
    from isaacsim.robot.policy.examples.robots.h1 import H1FlatTerrainPolicy
except Exception:  # pragma: no cover - Isaac Sim environment specific
    H1FlatTerrainPolicy = None  # type: ignore[assignment]
import base64
from io import BytesIO
from pxr import UsdGeom
from pxr import Usd
from pxr import UsdPhysics
try:
    # Some Isaac/PhysX-authored robot USDs tag articulation roots using PhysxSchema APIs rather than UsdPhysics.
    from pxr import PhysxSchema  # type: ignore
except Exception:  # pragma: no cover - depends on Isaac build
    PhysxSchema = None  # type: ignore
# IMPORTANT:
# Do NOT import PhysX sensor wrapper classes (IMUSensor/ContactSensor) at module import time.
# In some Isaac Sim startup sequences that can trigger PhysX tensor/simulation-view initialization
# before a USD stage / physics scene is attached, leading to:
#   - "No USD stage attached."
#   - "Failed to create simulation view: no active physics scene found"
# We lazy-import these wrappers only when physics is stepping and sensors are explicitly enabled.


@dataclass(frozen=True)
class WalkerConfig:
    # Base command limits
    vx: float = 0.8
    wz_gain: float = 1.8
    wz_max: float = 1.2

    # Stop condition near goal
    stop_radius: float = 0.6

    # Control mode:
    # - "goal": steer toward /gil/task/goal_{x,y}
    # - "external": use latest cmd_vel received from websocket (MCP)
    mode: str = "goal"
    cmd_timeout_s: float = 0.6

    # Robot prim
    prim_path: str = "/World/Humanoid"
    # Robot variant (affects controller availability)
    variant: str = "h1"

    # If true, try to initialize the Isaac-shipped H1 locomotion policy even when variant != "h1".
    # Useful when loading a close variant (e.g., H1-2) where joint layout may still be compatible.
    force_h1_policy: bool = False

    # If true, require a true locomotion controller (policy). Disables all "cheat" fallbacks
    # (base-velocity and kinematic stall recovery). This is what you want when you care about
    # footsteps matching translation.
    require_policy: bool = False

    # If true, and no locomotion policy is active, drive the humanoid by setting base rigid-body velocities.
    # This is a fallback for navigation experiments (not physically-correct walking).
    fallback_base_velocity: bool = True

    # If true, and no locomotion policy is active, also run a simple joint-space gait (visual walking).
    fallback_simple_gait: bool = True

    # ROS2 integration (runs inside Isaac Sim using Isaac's ROS2 bridge / internal rclpy).
    # This is what makes `/cmd_vel` move the humanoid in "external" mode and provides `/odom`.
    ros2_enable: bool = True
    ros2_cmd_vel_topic: str = "/cmd_vel"
    ros2_odom_topic: str = "/odom"
    ros2_mode_topic: str = "/humanoid/mode"
    ros2_frame_id: str = "odom"
    ros2_child_frame_id: str = "base_link"
    ros2_odom_hz: float = 30.0


class Extension(omni.ext.IExt):
    def _ensure_core_fields(self) -> None:
        """
        Isaac extensions can be hot-reloaded while background asyncio tasks are still running.
        Be defensive: ensure core fields exist before any task uses them.
        """
        # Always ensure these attributes exist, even if Kit/timeline isn't ready yet.
        lock_obj = None
        try:
            lock_obj = getattr(self, "_lock", None)
        except Exception:
            lock_obj = None
        if lock_obj is None:
            try:
                self._lock = Lock()
            except Exception:
                # Last resort: import locally and try again.
                from threading import Lock as _Lock

                self._lock = _Lock()

        try:
            tl = getattr(self, "_timeline", None)
        except Exception:
            tl = None
        if tl is None:
            try:
                self._timeline = omni.timeline.get_timeline_interface()
            except Exception:
                # Timeline may be unavailable in some startup sequences; callers must handle None.
                self._timeline = None

    def _prim_range(self, root: Usd.Prim):
        """
        Iterate prims under `root`, attempting to include instance proxies if supported.
        """
        try:
            try:
                it = Usd.PrimRange(root, Usd.TraverseInstanceProxies())
            except Exception:
                it = Usd.PrimRange(root)
            for p in it:
                yield p
        except Exception:
            return

    def _resolve_dc_articulation_and_base(self, dc, root_prim_path: str) -> tuple[str, Any, str, Any]:
        """
        Best-effort resolution of:
        - dynamic-control articulation handle
        - a "base" rigid-body handle (prefer pelvis)
        Returns (art_path, art_handle, base_path, base_handle). Any handle may be None.
        """
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return (root_prim_path, None, "", None)

        # 1) Resolve articulation (best-effort):
        # - Try common roots first (instance root, root_joint, etc.)
        # - Prefer authored ArticulationRootAPI if present
        # - As a last resort, probe a limited number of prim paths under the humanoid prefix.
        art_candidates: list[str] = [
            root_prim_path,
            f"{root_prim_path}/root_joint",
            f"{root_prim_path}/RootJoint",
            f"{root_prim_path}/robot",
            f"{root_prim_path}/Robot",
        ]
        try:
            root = stage.GetPrimAtPath(root_prim_path)
            if root and root.IsValid():
                for p in self._prim_range(root):
                    try:
                        if not (p and p.IsValid()):
                            continue
                        has_usd = False
                        has_physx = False
                        try:
                            has_usd = bool(p.HasAPI(UsdPhysics.ArticulationRootAPI))
                        except Exception:
                            has_usd = False
                        try:
                            if PhysxSchema is not None:
                                has_physx = bool(p.HasAPI(PhysxSchema.PhysxArticulationRootAPI))
                        except Exception:
                            has_physx = False
                        if has_usd or has_physx:
                            art_candidates.append(str(p.GetPath()))
                    except Exception:
                        continue
        except Exception:
            pass

        art_path = root_prim_path
        art_handle = None
        for cand in art_candidates:
            try:
                h = dc.get_articulation(cand)
                if h:
                    art_path = cand
                    art_handle = h
                    break
            except Exception:
                continue

        if art_handle is None:
            # Last resort (API-based): scan stage for articulation roots under the humanoid prefix.
            try:
                prefix = root_prim_path.rstrip("/")
                for p in Usd.PrimRange(stage.GetPseudoRoot()):
                    try:
                        if not (p and p.IsValid()):
                            continue
                        if not str(p.GetPath()).startswith(prefix):
                            continue
                        has_usd = False
                        has_physx = False
                        try:
                            has_usd = bool(p.HasAPI(UsdPhysics.ArticulationRootAPI))
                        except Exception:
                            has_usd = False
                        try:
                            if PhysxSchema is not None:
                                has_physx = bool(p.HasAPI(PhysxSchema.PhysxArticulationRootAPI))
                        except Exception:
                            has_physx = False
                        if not (has_usd or has_physx):
                            continue
                        cand = str(p.GetPath())
                        h = dc.get_articulation(cand)
                        if h:
                            art_path = cand
                            art_handle = h
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        if art_handle is None:
            # Final resort (probe-based): some referenced/instanced assets don't expose ArticulationRootAPI
            # on the instance root. Probe a limited number of prim paths under the humanoid prefix.
            try:
                root = stage.GetPrimAtPath(root_prim_path)
                if root and root.IsValid():
                    probed = 0
                    for p in self._prim_range(root):
                        if probed >= 600:
                            break
                        probed += 1
                        try:
                            cand = str(p.GetPath())
                            # Heuristic filter: skip obviously irrelevant prims.
                            name_l = (p.GetName() or "").lower()
                            if any(k in name_l for k in ("visual", "mesh", "geom", "collision", "collider", "material")):
                                continue
                            h = dc.get_articulation(cand)
                            if h:
                                art_path = cand
                                art_handle = h
                                break
                        except Exception:
                            continue
            except Exception:
                pass

        # 2) Resolve base rigid body: prefer pelvis, but assets vary (case, nesting, instancing).
        base_candidates: list[str] = []
        for prefix in [root_prim_path, art_path]:
            if prefix:
                base_candidates.append(f"{prefix}/pelvis")
                base_candidates.append(f"{prefix}/Pelvis")
                base_candidates.append(f"{prefix}/base")
                base_candidates.append(f"{prefix}/base_link")
                base_candidates.append(f"{prefix}/Base")
                base_candidates.append(f"{prefix}/BaseLink")
        try:
            root = stage.GetPrimAtPath(root_prim_path)
            if root and root.IsValid():
                for p in self._prim_range(root):
                    try:
                        if (p.GetName() or "").lower() == "pelvis":
                            base_candidates.append(str(p.GetPath()))
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        base_path = ""
        base_handle = None
        for cand in base_candidates:
            try:
                h = dc.get_rigid_body(cand)
                if h:
                    base_path = cand
                    base_handle = h
                    break
            except Exception:
                continue

        # Fallback: sometimes the humanoid root itself is a rigid body we can drive.
        if base_handle is None:
            try:
                h = dc.get_rigid_body(root_prim_path)
                if h:
                    base_path = root_prim_path
                    base_handle = h
            except Exception:
                pass

        if base_handle is None:
            # Final resort: pick the first rigid body under the humanoid prefix.
            # This is intentionally broad so variants that don't have /pelvis can still be driven.
            try:
                root = stage.GetPrimAtPath(root_prim_path)
                if root and root.IsValid():
                    probed = 0
                    for p in self._prim_range(root):
                        if probed >= 1200:
                            break
                        probed += 1
                        try:
                            cand = str(p.GetPath())
                            h = dc.get_rigid_body(cand)
                            if h:
                                base_path = cand
                                base_handle = h
                                break
                        except Exception:
                            continue
            except Exception:
                pass

        return (art_path, art_handle, base_path, base_handle)

    def on_startup(self, ext_id: str) -> None:
        # NOTE: some launch setups only persist warnings/errors into captured stdout logs.
        # Use warn-level breadcrumbs for critical bringup/debug so we can diagnose control-chain issues.
        carb.log_warn(f"[gil.h1_maze_walker] startup ext_id={ext_id}")
        self._ensure_core_fields()
        self._world: World | None = None
        self._timeline = omni.timeline.get_timeline_interface()
        self._h1: Any | None = None
        self._using_h1_policy: bool = False

        # Dynamic control fallback for non-policy variants (best-effort)
        self._dc = None
        self._dc_art = None
        self._dc_base_body = None
        self._dc_art_path: str = ""
        self._dc_base_path: str = ""
        self._gait_enabled: bool = False
        self._gait_t: float = 0.0
        self._gait_dofs: dict[str, Any] = {}
        # Rate-limit warn-level debug breadcrumbs.
        self._dbg_last_warn_t: float = 0.0

        # MCP/Websocket bridge state
        self._ws_task: asyncio.Task | None = None
        self._mode_override: str | None = None
        self._lock = Lock()
        self._latest_cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        self._latest_cmd_t = 0.0
        self._pending_state: dict | None = None
        self._pending_image: dict | None = None
        self._pending_reset: dict | None = None
        self._last_state_send_t = 0.0
        self._last_image_send_t = 0.0
        self._rgb_annot_fpv = None
        self._rgb_annot_tpv = None
        self._cam_task: asyncio.Task | None = None
        self._rep = None
        self._img_debug_logged = False
        self._rp_fpv_path: str | None = None
        self._rp_tpv_path: str | None = None
        self._rp_by_cam: dict[str, str] = {}
        self._rgb_by_cam: dict[str, Any] = {}
        self._last_fpv_update_t = 0.0
        # Kinematic fallback when timeline isn't playing (visual movement even while paused)
        self._last_kin_update_t = 0.0
        # Detect "stalled" motion (cmd_vel non-zero but reported base pose not changing) and
        # apply kinematic USD motion as a last-resort fallback even while playing.
        self._stall_check_t = 0.0
        self._stall_check_pose: tuple[float, float, float, float] | None = None
        self._imu_sensor: Any | None = None
        self._contact_sensors: dict[str, Any] = {}
        self._sensors_debug_logged = False
        self._sensors_err_logged = False
        # Cache last base pose/cmd from physics thread; build payloads on the async send loop thread.
        self._last_base_pose = (0.0, 0.0, 0.0, 0.0)  # x, y, z, yaw
        self._last_cmd_cache = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        self._last_pose_update_t = 0.0
        self._last_sensors_read_t = 0.0
        self._sensors_disabled_until_t = 0.0

        # ROS2 node (inside Isaac Sim). This is required for ROS2 mode to work end-to-end:
        # - subscribe `/cmd_vel` and feed the walker
        # - publish `/odom` for the controls stack
        self._ros2_started: bool = False
        self._ros2_stop = Event()
        self._ros2_thread: Thread | None = None
        self._ros2_node = None
        self._ros2_pub_odom = None
        self._ros2_sub_cmd_vel = None
        self._ros2_sub_mode = None
        self._ros2_last_odom_pub_t = 0.0

        self._task = asyncio.ensure_future(self._setup_when_ready())
        self._physics_cb_registered = False

    def _stage_has_physics_scene(self) -> bool:
        """
        Creating IMUSensor/ContactSensor objects can implicitly create PhysX tensor simulation views.
        If there is no UsdPhysics.Scene yet, Isaac will throw "no active physics scene found" and can
        wedge streaming sessions. Guard all sensor creation/reads behind this.
        """
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return False
            for p in Usd.PrimRange(stage.GetPseudoRoot()):
                if p.IsA(UsdPhysics.Scene):
                    return True
            return False
        except Exception:
            return False

    async def _setup_when_ready(self) -> None:
        self._ensure_core_fields()
        app = omni.kit.app.get_app()

        # Wait for stage
        for _ in range(600):
            if omni.usd.get_context().get_stage() is not None:
                break
            await app.next_update_async()

        # If the extension/stage was hot-reloaded, cached Isaac Core "World"/SimulationContext and
        # physics sensor objects can retain invalidated PhysX tensor views and spam errors like:
        #   omni::physx::tensors::CpuRigidBodyView::getVelocities ... Simulation view object is invalidated
        # Always clear previous state before re-initializing.
        # Do NOT call remove_physics_callback here: Isaac logs an error even if we catch exceptions.
        # Clearing the singleton world is enough; any stale callbacks will be dropped with it.
        try:
            self._world = None
            self._physics_cb_registered = False
            try:
                World.clear_instance()  # type: ignore[attr-defined]
            except Exception:
                pass
        except Exception:
            pass
        self._imu_sensor = None
        self._contact_sensors = {}
        self._sensors_debug_logged = False
        self._sensors_err_logged = False

        cfg = self._read_cfg()

        # Start ROS2 I/O early (doesn't require physics). Without this, ROS2 mode will have
        # `/cmd_vel` with no subscribers and `/odom` with no publishers.
        self._ensure_ros2_started(cfg)

        # Optional: auto-start simulation so physics/sensors actually step (headless/streaming).
        # NOTE: Isaac may reset the timeline state during sim-context initialization, so we
        # re-apply "play" after `initialize_simulation_context_async()` too.
        auto_play = False
        try:
            s = carb.settings.get_settings()
            v = s.get("/gil/humanoid/auto_play")
            auto_play = False if v is None else bool(v)
            if auto_play and self._timeline is not None and not self._timeline.is_playing():
                carb.log_info("[gil.h1_maze_walker] auto_play enabled: starting timeline (pre-init)")
                self._timeline.play()
        except Exception as e:
            carb.log_warn(f"[gil.h1_maze_walker] auto_play check failed: {e!r}")

        # Start websocket bridge ASAP so controls MCP can see a humanoid client even before Play.
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.ensure_future(self._ws_bridge_loop())

        # Setup camera capture ASAP (works even if paused; best-effort).
        # Replicator/cameras can come up late, so we retry until it succeeds.
        if self._cam_task is None or self._cam_task.done():
            self._cam_task = asyncio.ensure_future(self._camera_capture_retry_loop())

        # NOTE: we intentionally don't force-create physics sensor objects here.
        # They can create internal PhysX tensor "views" that become invalidated across resets.
        # We'll (re)discover them lazily when physics is actually stepping.

        # Create/attach a World so we can register a physics callback cleanly.
        # We don't create a new stage; we just wrap the existing one.
        #
        # IMPORTANT: don't initialize Isaac Core simulation context until a physics scene exists.
        # Otherwise IsaacSim may try to create a PhysX tensors SimulationView while no USD stage/scene
        # is attached yet, producing errors like:
        #   [omni.physx.plugin] No USD stage attached.
        #   Failed to create simulation view: no active physics scene found
        #
        # If the physics scene never appears, we can still stream cameras and accept cmd_vel; users
        # will just see kinematic (USD) motion instead of physics-driven motion.
        physics_ready = False
        for _ in range(600):  # ~10s at 60 Hz
            if self._stage_has_physics_scene():
                physics_ready = True
                break
            await app.next_update_async()
        if not physics_ready:
            carb.log_warn("[gil.h1_maze_walker] No UsdPhysics.Scene detected yet; skipping World/sim-context init to avoid PhysX tensors errors.")
            return

        try:
            World.clear_instance()  # type: ignore[attr-defined]
        except Exception:
            pass
        self._world = World(stage_units_in_meters=1.0, physics_dt=1 / 200, rendering_dt=2 / 200)
        await self._world.initialize_simulation_context_async()

        # Isaac can stop the timeline as part of simulation-context init. Re-apply auto_play here.
        if auto_play and self._timeline is not None and not self._timeline.is_playing():
            carb.log_info("[gil.h1_maze_walker] auto_play enabled: starting timeline (post-init)")
            self._timeline.play()
            # Give Kit a tick to transition states.
            await app.next_update_async()

        if not auto_play:
            carb.log_info("[gil.h1_maze_walker] Waiting for user to press Play (timeline)")
            # Wait until timeline is playing (user pressed Play in the UI)
            while not self._timeline.is_playing():
                await app.next_update_async()
        else:
            carb.log_info("[gil.h1_maze_walker] auto_play enabled: skipping Play wait")

        # Initialize controller:
        # - Prefer the Isaac-shipped H1 policy when variant == "h1"
        # - Optionally try it for other variants (e.g. h1_2) when force_h1_policy is enabled.
        self._using_h1_policy = False
        self._h1 = None
        want_h1_policy = (cfg.variant == "h1") or bool(cfg.force_h1_policy)
        if want_h1_policy and H1FlatTerrainPolicy is not None:
            try:
                self._h1 = H1FlatTerrainPolicy(prim_path=cfg.prim_path, name="humanoid_h1")
                await app.next_update_async()
                self._h1.initialize()
                await app.next_update_async()
                self._using_h1_policy = True
                carb.log_info(f"[gil.h1_maze_walker] locomotion_controller=H1FlatTerrainPolicy prim={cfg.prim_path} variant={cfg.variant}")
            except Exception as e:
                self._h1 = None
                self._using_h1_policy = False
                carb.log_warn(f"[gil.h1_maze_walker] H1FlatTerrainPolicy init failed (variant={cfg.variant}): {e!r}")
        else:
            if want_h1_policy and H1FlatTerrainPolicy is None:
                carb.log_warn("[gil.h1_maze_walker] H1FlatTerrainPolicy not available in this Isaac Sim build")

        # Setup dynamic-control fallback if requested and no policy is active.
        self._dc = None
        self._dc_art = None
        self._dc_base_body = None
        if (not self._using_h1_policy) and bool(cfg.fallback_base_velocity):
            try:
                from omni.isaac.dynamic_control import _dynamic_control  # type: ignore

                self._dc = _dynamic_control.acquire_dynamic_control_interface()
                art_path, art_h, base_path, base_h = self._resolve_dc_articulation_and_base(self._dc, str(cfg.prim_path))
                self._dc_art = art_h
                self._dc_base_body = base_h
                self._dc_art_path = str(art_path or "")
                self._dc_base_path = str(base_path or "")
                # Warn-level so it shows up in captured logs.
                carb.log_warn(
                    f"[gil.h1_maze_walker] locomotion_controller=fallback_base_velocity enabled={bool(self._dc_base_body)} "
                    f"art={self._dc_art_path or '<none>'} base={self._dc_base_path or '<none>'}"
                )
                if self._dc_art is None:
                    carb.log_warn(f"[gil.h1_maze_walker] fallback_base_velocity: no articulation found under {cfg.prim_path}")
                if self._dc_base_body is None:
                    carb.log_warn(f"[gil.h1_maze_walker] fallback_base_velocity: no rigid body found under {cfg.prim_path} (pelvis/root)")
            except Exception as e:
                self._dc = None
                self._dc_art = None
                self._dc_base_body = None
                self._dc_art_path = ""
                self._dc_base_path = ""
                carb.log_warn(f"[gil.h1_maze_walker] fallback_base_velocity init failed: {e!r}")

        # Optional: simple gait (joint oscillation) to make fallback locomotion look like walking.
        self._gait_enabled = False
        self._gait_dofs = {}
        self._gait_t = 0.0
        if (not self._using_h1_policy) and self._dc is not None and self._dc_art is not None and bool(cfg.fallback_simple_gait):
            self._gait_enabled = self._init_simple_gait_dofs(cfg)

        self._world.add_physics_callback("gil_h1_maze_walker_step", callback_fn=self._on_physics_step)
        self._physics_cb_registered = True

        carb.log_warn("[gil.h1_maze_walker] Controller initialized")

    def _init_simple_gait_dofs(self, cfg: WalkerConfig) -> bool:
        """
        Best-effort lookup of leg joint DOFs for a simple gait oscillator.
        Returns True if we found at least a minimal set of DOFs to drive.
        """
        try:
            if self._dc is None or self._dc_art is None:
                return False

            joint_names = [
                # left leg
                "left_hip_yaw_joint",
                "left_hip_roll_joint",
                "left_hip_pitch_joint",
                "left_knee_joint",
                "left_ankle_pitch_joint",
                "left_ankle_roll_joint",
                # right leg
                "right_hip_yaw_joint",
                "right_hip_roll_joint",
                "right_hip_pitch_joint",
                "right_knee_joint",
                "right_ankle_pitch_joint",
                "right_ankle_roll_joint",
            ]

            dofs: dict[str, Any] = {}
            for j in joint_names:
                try:
                    dof = self._dc.find_articulation_dof(self._dc_art, j)
                except Exception:
                    dof = None
                if dof:
                    dofs[j] = dof

            required = [
                "left_hip_pitch_joint",
                "left_knee_joint",
                "left_ankle_pitch_joint",
                "right_hip_pitch_joint",
                "right_knee_joint",
                "right_ankle_pitch_joint",
            ]
            if not all(r in dofs for r in required):
                carb.log_warn(
                    f"[gil.h1_maze_walker] fallback_simple_gait: missing required DOFs. found={sorted(dofs.keys())}"
                )
                return False

            self._gait_dofs = dofs
            carb.log_info(
                f"[gil.h1_maze_walker] locomotion_controller=fallback_simple_gait enabled=True dofs={len(dofs)} variant={cfg.variant}"
            )
            return True
        except Exception as e:
            carb.log_warn(f"[gil.h1_maze_walker] fallback_simple_gait init failed: {e!r}")
            return False

    def _apply_simple_gait(self, cmd: np.ndarray, dt: float) -> None:
        """
        Very simple gait oscillator for visual walking.
        - Drives hip_pitch/knee/ankle_pitch primarily.
        - Uses cmd[0]=vx, cmd[1]=vy, cmd[2]=wz in robot frame.
        """
        if not self._gait_enabled or self._dc is None or self._dc_art is None or not self._gait_dofs:
            return

        self._gait_t += float(dt)

        vx = float(cmd[0])
        vy = float(cmd[1])
        wz = float(cmd[2])

        speed = float(min(1.0, max(0.0, abs(vx) + 0.5 * abs(vy) + 0.25 * abs(wz))))
        if speed < 1e-3:
            stand = {
                "left_hip_pitch_joint": -0.20,
                "left_knee_joint": 0.42,
                "left_ankle_pitch_joint": -0.23,
                "right_hip_pitch_joint": -0.20,
                "right_knee_joint": 0.42,
                "right_ankle_pitch_joint": -0.23,
                "left_hip_roll_joint": 0.00,
                "right_hip_roll_joint": 0.00,
                "left_hip_yaw_joint": 0.00,
                "right_hip_yaw_joint": 0.00,
                "left_ankle_roll_joint": 0.00,
                "right_ankle_roll_joint": 0.00,
            }
            for name, target in stand.items():
                dof = self._gait_dofs.get(name)
                if dof:
                    try:
                        self._dc.set_dof_position_target(dof, float(target))
                    except Exception:
                        pass
            return

        freq_hz = 1.2 + 1.0 * speed
        phase = 2.0 * math.pi * freq_hz * self._gait_t
        ph_l = phase
        ph_r = phase + math.pi

        stride = 0.35 * max(-1.0, min(1.0, vx))
        lift = 0.55 * speed
        roll = 0.08 * max(-1.0, min(1.0, vy))
        yaw_swing = 0.10 * max(-1.0, min(1.0, wz))

        def leg_targets(ph: float, side: str) -> dict[str, float]:
            s = math.sin(ph)
            c = math.cos(ph)
            swing = max(0.0, s)
            hip_pitch = -0.20 + stride * s
            knee = 0.42 + lift * swing
            ankle_pitch = -0.23 - 0.6 * lift * swing
            hip_roll = (roll if side == "left" else -roll) + 0.03 * c
            ankle_roll = -(hip_roll * 0.8)
            hip_yaw = (yaw_swing if side == "left" else -yaw_swing)
            return {
                f"{side}_hip_pitch_joint": hip_pitch,
                f"{side}_knee_joint": knee,
                f"{side}_ankle_pitch_joint": ankle_pitch,
                f"{side}_hip_roll_joint": hip_roll,
                f"{side}_ankle_roll_joint": ankle_roll,
                f"{side}_hip_yaw_joint": hip_yaw,
            }

        targets: dict[str, float] = {}
        targets.update(leg_targets(ph_l, "left"))
        targets.update(leg_targets(ph_r, "right"))
        for name, target in targets.items():
            dof = self._gait_dofs.get(name)
            if dof:
                try:
                    self._dc.set_dof_position_target(dof, float(target))
                except Exception:
                    pass

    def _ensure_physics_sensors_from_settings(self) -> None:
        """
        Connect to sensor prims created by gil.unitree_h1_scene (or create them if present in stage).
        """
        try:
            s = carb.settings.get_settings()
            enable = s.get("/gil/sensors/enable")
            enable = True if enable is None else bool(enable)
            if not enable:
                # Hard-disable sensors: do not instantiate wrappers (they can touch PhysX tensors).
                self._imu_sensor = None
                self._contact_sensors = {}
                self._sensors_payload = {}
                return
            imu_path = s.get("/gil/sensors/imu_prim_path")
            imu_path = "" if imu_path is None else str(imu_path).strip()
            contacts_json = s.get("/gil/sensors/contact_prim_paths_json")
            contacts_json = "" if contacts_json is None else str(contacts_json).strip()
        except Exception:
            s = carb.settings.get_settings()
            imu_path = ""
            contacts_json = ""

        if imu_path and self._imu_sensor is None:
            try:
                self._imu_sensor = IMUSensor(prim_path=imu_path)
            except Exception:
                self._imu_sensor = None

        if contacts_json and not self._contact_sensors:
            try:
                d = json.loads(contacts_json)
            except Exception:
                d = {}
            if isinstance(d, dict):
                include_raw = s.get("/gil/sensors/include_raw_contacts")
                include_raw = False if include_raw is None else bool(include_raw)
                for key, path in d.items():
                    try:
                        cs = ContactSensor(prim_path=str(path))
                        if include_raw:
                            try:
                                cs.add_raw_contact_data_to_frame()
                            except Exception:
                                pass
                        self._contact_sensors[str(key)] = cs
                    except Exception:
                        continue

        # If settings aren't populated yet, try the known default paths from our sensor_attachment helper.
        if self._imu_sensor is None:
            try:
                stage = omni.usd.get_context().get_stage()
                p = None if stage is None else stage.GetPrimAtPath("/World/Humanoid/pelvis/imu_sensor")
                if p and p.IsValid():
                    self._imu_sensor = IMUSensor(prim_path="/World/Humanoid/pelvis/imu_sensor")
            except Exception:
                self._imu_sensor = None

        if not self._contact_sensors:
            defaults = {
                "torso": "/World/Humanoid/pelvis/contact_sensor",
                "left_foot": "/World/Humanoid/left_ankle_pitch_link/contact_sensor",
                "right_foot": "/World/Humanoid/right_ankle_pitch_link/contact_sensor",
                "left_hand": "/World/Humanoid/left_wrist_roll_link/contact_sensor",
                "right_hand": "/World/Humanoid/right_wrist_roll_link/contact_sensor",
            }
            try:
                stage = omni.usd.get_context().get_stage()
                for k, path in defaults.items():
                    p = None if stage is None else stage.GetPrimAtPath(path)
                    if not (p and p.IsValid()):
                        continue
                    try:
                        self._contact_sensors[k] = ContactSensor(prim_path=path)
                    except Exception:
                        continue
            except Exception:
                pass

        # Fallback: scan the stage for Isaac sensor prims (handles timing issues across extensions).
        if self._imu_sensor is None or not self._contact_sensors:
            try:
                stage = omni.usd.get_context().get_stage()
                root = None if stage is None else stage.GetPrimAtPath("/World/Humanoid")
                if root and root.IsValid():
                    imu_candidates: list[str] = []
                    contact_candidates: list[str] = []
                    for p in Usd.PrimRange(root):
                        tn = (p.GetTypeName() or "").lower()
                        if "isaacimusensor" in tn:
                            imu_candidates.append(p.GetPath().pathString)
                        if "isaaccontactsensor" in tn:
                            contact_candidates.append(p.GetPath().pathString)

                    if self._imu_sensor is None and imu_candidates:
                        try:
                            self._imu_sensor = IMUSensor(prim_path=sorted(imu_candidates)[0])
                        except Exception:
                            self._imu_sensor = None

                    if not self._contact_sensors and contact_candidates:
                        include_raw = s.get("/gil/sensors/include_raw_contacts")
                        include_raw = False if include_raw is None else bool(include_raw)

                        def _key_for(path: str) -> str:
                            pl = path.lower()
                            if "left" in pl and ("ankle" in pl or "foot" in pl):
                                return "left_foot"
                            if "right" in pl and ("ankle" in pl or "foot" in pl):
                                return "right_foot"
                            if "left" in pl and ("wrist" in pl or "hand" in pl):
                                return "left_hand"
                            if "right" in pl and ("wrist" in pl or "hand" in pl):
                                return "right_hand"
                            if "torso" in pl or "pelvis" in pl or "imu" in pl:
                                return "torso"
                            return "contact"

                        for cp in sorted(contact_candidates):
                            key = _key_for(cp)
                            # If multiple match the same key, suffix them.
                            if key in self._contact_sensors:
                                i = 2
                                while f"{key}_{i}" in self._contact_sensors:
                                    i += 1
                                key = f"{key}_{i}"
                            try:
                                cs = ContactSensor(prim_path=cp)
                                if include_raw:
                                    try:
                                        cs.add_raw_contact_data_to_frame()
                                    except Exception:
                                        pass
                                self._contact_sensors[key] = cs
                            except Exception:
                                continue
            except Exception:
                pass

        if not self._sensors_debug_logged:
            self._sensors_debug_logged = True
            try:
                contacts_map = {key: cs.prim_path for key, cs in self._contact_sensors.items()}
                carb.log_info(
                    f"[gil.h1_maze_walker] sensors_discovery imu={getattr(self._imu_sensor,'prim_path',None)} contacts={contacts_map}"
                )
            except Exception as e:
                carb.log_warn(f"[gil.h1_maze_walker] sensors_discovery failed to log: {e!r}")
        return

    async def _wait_for_camera_prims(self, timeout_updates: int = 600) -> None:
        """
        Wait for camera prims created by `gil.unitree_h1_scene`.
        """
        app = omni.kit.app.get_app()
        for _ in range(int(timeout_updates)):
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                fpv = stage.GetPrimAtPath("/World/Humanoid/FPVCamera")
                # Wide camera might be the streamed viewport camera; prefer that if it exists.
                tpv = stage.GetPrimAtPath("/OmniverseKit_Persp")
                if not (tpv and tpv.IsValid()):
                    tpv = stage.GetPrimAtPath("/World/ThirdPersonCamera")
                if fpv and fpv.IsValid() and tpv and tpv.IsValid():
                    return
            await app.next_update_async()

    async def _camera_capture_retry_loop(self) -> None:
        app = omni.kit.app.get_app()
        backoff = 0.25
        while True:
            if self._rgb_annot_fpv is not None and self._rgb_annot_tpv is not None:
                return
            try:
                await self._wait_for_camera_prims(timeout_updates=120)
                self._setup_camera_capture()
                if self._rgb_annot_fpv is not None and self._rgb_annot_tpv is not None:
                    return
            except Exception as e:
                # Keep it quiet-ish; this is expected during startup.
                carb.log_warn(f"[gil.h1_maze_walker] Camera capture retry failed: {e!r}")
            await app.next_update_async()
            await asyncio.sleep(backoff)
            backoff = min(2.0, backoff * 1.5)

    def _setup_camera_capture(self) -> None:
        """
        Create Replicator render products and RGB annotators for:
        - /World/Humanoid/FPVCamera (main)
        - /World/ThirdPersonCamera (wide)
        """
        try:
            import omni.replicator.core as rep
        except Exception:
            carb.log_warn("[gil.h1_maze_walker] omni.replicator.core not available; images disabled")
            return
        self._rep = rep

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        s = carb.settings.get_settings()
        fpv_path = s.get("/gil/camera/fpv_prim_path")
        fpv_path = "/World/Humanoid/FPVCamera" if fpv_path is None else str(fpv_path)
        # Wide camera: prefer the streamed viewport camera if present (most reliable),
        # otherwise fall back to the fixed third-person camera.
        tpv_path = "/OmniverseKit_Persp"
        if stage.GetPrimAtPath(tpv_path) is None or not stage.GetPrimAtPath(tpv_path).IsValid():
            tpv_path = "/World/ThirdPersonCamera"

        # Ensure camera prims exist before creating render products.
        fpv_prim = stage.GetPrimAtPath(fpv_path)
        tpv_prim = stage.GetPrimAtPath(tpv_path)
        if not (fpv_prim and fpv_prim.IsValid() and tpv_prim and tpv_prim.IsValid()):
            raise RuntimeError(f"Camera prims not ready (fpv={bool(fpv_prim and fpv_prim.IsValid())} tpv={bool(tpv_prim and tpv_prim.IsValid())})")

        w = s.get("/gil/camera/fpv_width")
        h = s.get("/gil/camera/fpv_height")
        w = 640 if w is None else int(w)
        h = 360 if h is None else int(h)
        ww = s.get("/gil/camera/wide_width")
        wh = s.get("/gil/camera/wide_height")
        ww = 640 if ww is None else int(ww)
        wh = 360 if wh is None else int(wh)

        # Create render products
        rp_fpv = rep.create.render_product(fpv_path, (w, h))
        rp_tpv = rep.create.render_product(tpv_path, (ww, wh))
        self._rp_fpv_path = rp_fpv.path
        self._rp_tpv_path = rp_tpv.path

        rgb_fpv = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb_tpv = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb_fpv.attach([rp_fpv])
        rgb_tpv.attach([rp_tpv])

        self._rgb_annot_fpv = rgb_fpv
        self._rgb_annot_tpv = rgb_tpv

        # Optional: capture all robot cameras (front/world/wrist) as additional streams.
        capture_all = s.get("/gil/camera/capture_all")
        capture_all = True if capture_all is None else bool(capture_all)
        self._rp_by_cam = {}
        self._rgb_by_cam = {}
        if capture_all:
            try:
                stage = omni.usd.get_context().get_stage()
                root = None if stage is None else stage.GetPrimAtPath("/World/Humanoid")
                cam_paths: list[str] = []
                if root and root.IsValid():
                    for p in Usd.PrimRange(root):
                        if p.IsA(UsdGeom.Camera):
                            cam_paths.append(p.GetPath().pathString)
                # Keep it bounded
                cam_paths = sorted(set(cam_paths))[:8]
                for cp in cam_paths:
                    if cp == fpv_path:
                        continue
                    try:
                        rp = rep.create.render_product(cp, (w, h))
                        rgb = rep.AnnotatorRegistry.get_annotator("rgb")
                        rgb.attach([rp])
                        self._rp_by_cam[cp] = rp.path
                        self._rgb_by_cam[cp] = rgb
                    except Exception:
                        continue
            except Exception:
                pass

        carb.log_info("[gil.h1_maze_walker] Camera capture enabled (FPV + TPV)")

    def _encode_jpeg_data_url(self, rgba) -> str:
        """
        Encode an RGBA/RGB numpy array to a data URL JPEG string.
        """
        try:
            import numpy as _np
            from PIL import Image

            # Replicator annotators sometimes return dict-like payloads.
            if isinstance(rgba, dict):
                rgba = rgba.get("data") or rgba.get("rgb") or rgba.get("buffer") or rgba
            arr = _np.asarray(rgba)

            # Handle float images in [0,1]
            if arr.dtype.kind == "f":
                arr = _np.clip(arr * 255.0, 0.0, 255.0).astype("uint8")
            else:
                arr = arr.astype("uint8", copy=False)

            if arr.ndim == 3 and arr.shape[2] >= 3:
                arr = arr[:, :, :3]
            img = Image.fromarray(arr.astype("uint8"))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return "data:image/jpeg;base64," + b64
        except Exception:
            return ""

    def _goal_xy(self) -> tuple[float, float]:
        s = carb.settings.get_settings()
        gx = s.get("/gil/task/goal_x")
        gy = s.get("/gil/task/goal_y")
        gx = 3.5 if gx is None else float(gx)
        gy = 3.5 if gy is None else float(gy)
        return gx, gy

    def _read_cfg(self) -> WalkerConfig:
        s = carb.settings.get_settings()

        def gf(key: str, default: float) -> float:
            v = s.get(key)
            return default if v is None else float(v)

        def gs(key: str, default: str) -> str:
            v = s.get(key)
            return default if v is None else str(v)

        variant = gs("/gil/humanoid/variant", "h1").lower().strip()
        # For H1-2 we generally want "real" locomotion (policy) and do NOT want navigation-only base velocity hacks
        # unless explicitly enabled by the user.
        is_h12 = variant in ("h1_2", "h1-2", "h12", "h1_2.0", "h1_2_") or variant.startswith("h1_2")

        force_h1_policy_setting = s.get("/gil/walker/force_h1_policy")
        fallback_base_vel_setting = s.get("/gil/walker/fallback_base_velocity")
        fallback_simple_gait_setting = s.get("/gil/walker/fallback_simple_gait")
        require_policy_setting = s.get("/gil/walker/require_policy")

        # Default to NOT forcing the built-in H1 policy for H1-2.
        # In Isaac 5.1 this frequently fails with joint-layout/shape mismatches and produces noisy warnings.
        # Users can still opt-in by setting `/gil/walker/force_h1_policy=true`.
        force_h1_policy = bool(force_h1_policy_setting) if force_h1_policy_setting is not None else False
        # For H1-2: if the policy init fails, we still want visible walking instead of a mannequin sliding.
        # So default to enabling the dynamic-control + simple-gait fallback unless the user explicitly disables it.
        fallback_base_velocity = bool(fallback_base_vel_setting) if fallback_base_vel_setting is not None else True
        fallback_simple_gait = bool(fallback_simple_gait_setting) if fallback_simple_gait_setting is not None else True
        require_policy = bool(require_policy_setting) if require_policy_setting is not None else False

        # If the user explicitly requests "real locomotion only", disable all cheats.
        if require_policy:
            fallback_base_velocity = False
            fallback_simple_gait = False

        # ROS2 config (inside Isaac). Can be overridden via carb settings.
        ros2_enable_setting = s.get("/gil/ros2/enable")
        ros2_enable = True if ros2_enable_setting is None else bool(ros2_enable_setting)

        def gs_or(key: str, default: str) -> str:
            v = s.get(key)
            return default if v is None else str(v)

        ros2_cmd_vel_topic = gs_or("/gil/ros2/cmd_vel_topic", "/cmd_vel")
        ros2_odom_topic = gs_or("/gil/ros2/odom_topic", "/odom")
        ros2_mode_topic = gs_or("/gil/ros2/mode_topic", "/humanoid/mode")
        ros2_frame_id = gs_or("/gil/ros2/frame_id", "odom")
        ros2_child_frame_id = gs_or("/gil/ros2/child_frame_id", "base_link")
        ros2_odom_hz = gf("/gil/ros2/odom_hz", 30.0)

        return WalkerConfig(
            vx=gf("/gil/walker/vx", 0.8),
            wz_gain=gf("/gil/walker/wz_gain", 1.8),
            wz_max=gf("/gil/walker/wz_max", 1.2),
            stop_radius=gf("/gil/walker/stop_radius", 0.6),
            mode=gs("/gil/walker/mode", "goal").lower(),
            cmd_timeout_s=gf("/gil/walker/cmd_timeout_s", 0.6),
            prim_path=gs("/gil/walker/prim_path", "/World/Humanoid"),
            variant=variant,
            force_h1_policy=force_h1_policy,
            require_policy=require_policy,
            fallback_base_velocity=fallback_base_velocity,
            fallback_simple_gait=fallback_simple_gait,
            ros2_enable=ros2_enable,
            ros2_cmd_vel_topic=ros2_cmd_vel_topic,
            ros2_odom_topic=ros2_odom_topic,
            ros2_mode_topic=ros2_mode_topic,
            ros2_frame_id=ros2_frame_id,
            ros2_child_frame_id=ros2_child_frame_id,
            ros2_odom_hz=ros2_odom_hz,
        )

    def _ensure_ros2_started(self, cfg: WalkerConfig) -> None:
        if self._ros2_started:
            return
        if not bool(cfg.ros2_enable):
            return

        try:
            # These imports succeed only when Isaac's ROS2 bridge is enabled (isaacsim.ros2.bridge).
            import rclpy  # type: ignore
            from geometry_msgs.msg import Twist  # type: ignore
            from nav_msgs.msg import Odometry  # type: ignore
            from std_msgs.msg import String  # type: ignore
        except Exception as e:  # pragma: no cover - depends on Isaac environment
            carb.log_warn(f"[gil.h1_maze_walker] ROS2 not available in Isaac environment: {e!r}")
            return

        try:
            rclpy.init(args=None)
        except Exception:
            # rclpy can already be initialized in-process; treat that as OK.
            pass

        try:
            self._ros2_node = rclpy.create_node("gil_isaac_humanoid_walker")
            self._ros2_pub_odom = self._ros2_node.create_publisher(Odometry, str(cfg.ros2_odom_topic), 10)

            def _on_cmd(msg: Twist) -> None:
                try:
                    vx = float(msg.linear.x)
                    vy = float(msg.linear.y)
                    wz = float(msg.angular.z)
                except Exception:
                    return
                with self._lock:
                    self._latest_cmd = np.array([vx, vy, wz], dtype=np.float32)
                    self._latest_cmd_t = float(time.time())
                if abs(vx) + abs(vy) + abs(wz) > 1e-6:
                    # Safety/UX: any non-zero cmd_vel implies external driving intent.
                    self._mode_override = "external"
                    try:
                        if self._timeline is not None and (not self._timeline.is_playing()):
                            self._timeline.play()
                    except Exception:
                        pass

            def _on_mode(msg: String) -> None:
                try:
                    m = str(msg.data).strip().lower()
                except Exception:
                    return
                if m not in ("goal", "external"):
                    return
                self._mode_override = m
                try:
                    if self._timeline is not None and (not self._timeline.is_playing()):
                        self._timeline.play()
                except Exception:
                    pass

            # IMPORTANT: keep subscription objects alive (rclpy can GC them if not referenced).
            self._ros2_sub_cmd_vel = self._ros2_node.create_subscription(Twist, str(cfg.ros2_cmd_vel_topic), _on_cmd, 10)
            self._ros2_sub_mode = self._ros2_node.create_subscription(String, str(cfg.ros2_mode_topic), _on_mode, 10)

            self._ros2_stop.clear()
            self._ros2_thread = Thread(target=self._ros2_spin_thread, name="gil_isaac_ros2_spin", daemon=True)
            self._ros2_thread.start()
            self._ros2_started = True
            carb.log_info(
                "[gil.h1_maze_walker] ROS2 started: "
                f"cmd_vel={cfg.ros2_cmd_vel_topic} odom={cfg.ros2_odom_topic} mode={cfg.ros2_mode_topic}"
            )
        except Exception as e:  # pragma: no cover
            carb.log_warn(f"[gil.h1_maze_walker] Failed to start ROS2 node: {e!r}")
            self._ros2_started = False

    def _ros2_spin_thread(self) -> None:
        try:
            import rclpy  # type: ignore
            from rclpy.executors import SingleThreadedExecutor  # type: ignore
        except Exception:
            return
        if self._ros2_node is None:
            return
        try:
            ex = SingleThreadedExecutor()
            ex.add_node(self._ros2_node)
            while (not self._ros2_stop.is_set()) and rclpy.ok():
                ex.spin_once(timeout_sec=0.1)
            try:
                ex.shutdown()
            except Exception:
                pass
        except Exception:
            return

    def _ros2_publish_odom(self, cfg: WalkerConfig, x: float, y: float, z: float, yaw: float, cmd: np.ndarray) -> None:
        if (not self._ros2_started) or (self._ros2_node is None) or (self._ros2_pub_odom is None):
            return
        hz = float(cfg.ros2_odom_hz)
        if hz <= 0:
            return
        now = float(time.time())
        if (now - float(self._ros2_last_odom_pub_t)) < (1.0 / hz):
            return
        self._ros2_last_odom_pub_t = now
        try:
            from nav_msgs.msg import Odometry  # type: ignore
        except Exception:
            return
        try:
            msg = Odometry()
            msg.header.frame_id = str(cfg.ros2_frame_id)
            msg.child_frame_id = str(cfg.ros2_child_frame_id)
            try:
                msg.header.stamp = self._ros2_node.get_clock().now().to_msg()
            except Exception:
                pass
            msg.pose.pose.position.x = float(x)
            msg.pose.pose.position.y = float(y)
            msg.pose.pose.position.z = float(z)
            msg.pose.pose.orientation.x = 0.0
            msg.pose.pose.orientation.y = 0.0
            msg.pose.pose.orientation.z = float(math.sin(yaw * 0.5))
            msg.pose.pose.orientation.w = float(math.cos(yaw * 0.5))
            # cmd is in robot frame already
            msg.twist.twist.linear.x = float(cmd[0])
            msg.twist.twist.linear.y = float(cmd[1])
            msg.twist.twist.angular.z = float(cmd[2])
            self._ros2_pub_odom.publish(msg)
        except Exception:
            return

    def _on_physics_step(self, step_size: float) -> None:
        if not self._timeline.is_playing():
            return

        cfg = self._read_cfg()

        # Apply pending reset (best-effort).
        # Note: Different assets expose different "true" base links; we try a few mechanisms.
        reset_req = None
        with self._lock:
            if self._pending_reset is not None:
                reset_req = self._pending_reset
                self._pending_reset = None
        if isinstance(reset_req, dict):
            try:
                rx0 = float(reset_req.get("x", 0.0))
                ry0 = float(reset_req.get("y", 0.0))
                yaw0 = float(reset_req.get("yaw", 0.0))
                # Preserve a sane base height on reset.
                # Many humanoid assets expect the root pose to be at pelvis height; forcing z=0 can "drop" the robot,
                # leading to immediate collapse and making navigation impossible.
                z_keep = None
                # Prefer: if we have a policy robot handle, use its API.
                if self._h1 is not None and getattr(self._h1, "robot", None) is not None:
                    try:
                        try:
                            cur_pos, _cur_quat = self._h1.robot.get_world_pose()
                            z_keep = float(cur_pos[2])
                        except Exception:
                            z_keep = None
                        if z_keep is None or (not math.isfinite(float(z_keep))):
                            z_keep = 0.90
                        z_keep = float(max(0.60, z_keep))
                        pos = np.array([rx0, ry0, float(z_keep)], dtype=np.float32)
                        # Quaternion (w, x, y, z) for yaw about Z
                        cy = math.cos(yaw0 * 0.5)
                        sy = math.sin(yaw0 * 0.5)
                        quat = np.array([cy, 0.0, 0.0, sy], dtype=np.float32)
                        if hasattr(self._h1.robot, "set_world_pose"):
                            self._h1.robot.set_world_pose(pos, quat)
                    except Exception:
                        pass

                # Dynamic-control pose reset if available.
                if self._dc is not None and self._dc_base_body is not None:
                    try:
                        from omni.isaac.dynamic_control import _dynamic_control  # type: ignore

                        tf = _dynamic_control.Transform()
                        if z_keep is None:
                            try:
                                cur_tf = self._dc.get_rigid_body_pose(self._dc_base_body)
                                p = getattr(cur_tf, "p", None)
                                if p is not None:
                                    pz = getattr(p, "z", None)
                                    z_keep = float(p[2]) if pz is None else float(p.z)
                            except Exception:
                                z_keep = None
                        if z_keep is None or (not math.isfinite(float(z_keep))):
                            z_keep = 0.90
                        z_keep = float(max(0.60, z_keep))
                        tf.p = _dynamic_control.Vec3(rx0, ry0, float(z_keep))
                        cy = math.cos(yaw0 * 0.5)
                        sy = math.sin(yaw0 * 0.5)
                        tf.r = _dynamic_control.Quat(cy, 0.0, 0.0, sy)
                        self._dc.set_rigid_body_pose(self._dc_base_body, tf)
                        self._dc.set_rigid_body_linear_velocity(self._dc_base_body, (0.0, 0.0, 0.0))
                        self._dc.set_rigid_body_angular_velocity(self._dc_base_body, (0.0, 0.0, 0.0))
                    except Exception:
                        pass

                # USD authoring fallback (may not affect physics, but keeps stage consistent).
                try:
                    stage = omni.usd.get_context().get_stage()
                    prim = None if stage is None else stage.GetPrimAtPath(cfg.prim_path)
                    if prim and prim.IsValid():
                        xf = UsdGeom.Xformable(prim)
                        ops = xf.GetOrderedXformOps()
                        xlate = None
                        rot = None
                        for op in ops:
                            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                                xlate = op
                            if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
                                rot = op
                        if xlate is None:
                            xlate = xf.AddTranslateOp()
                        if rot is None:
                            rot = xf.AddRotateXYZOp()
                        z_u = 0.0 if z_keep is None else float(z_keep)
                        xlate.Set((rx0, ry0, z_u))
                        rot.Set((0.0, 0.0, float(yaw0 * 180.0 / math.pi)))
                except Exception:
                    pass
            except Exception:
                pass

        # Base pose (works with or without policy controller)
        rx = ry = rz = yaw = 0.0
        if self._h1 is not None and self._using_h1_policy:
            pos, quat = self._h1.robot.get_world_pose()
            rx, ry, rz = float(pos[0]), float(pos[1]), float(pos[2])
            yaw = float(quat_to_euler_angles(quat)[2])
        else:
            # Prefer dynamic-control pose when available; USD hierarchy often keeps the root Xform fixed,
            # which makes /World/Humanoid (and sometimes even /World/Humanoid/pelvis) appear "stuck".
            if self._dc is not None and self._dc_base_body is not None:
                try:
                    tf = self._dc.get_rigid_body_pose(self._dc_base_body)
                    p = getattr(tf, "p", None)
                    q = getattr(tf, "r", None)
                    if p is not None:
                        # Vec3-like (x,y,z) or indexable
                        px = getattr(p, "x", None)
                        if px is None:
                            rx, ry, rz = float(p[0]), float(p[1]), float(p[2])
                        else:
                            rx, ry, rz = float(p.x), float(p.y), float(p.z)
                    if q is not None:
                        # Quat-like; Isaac commonly uses (w,x,y,z)
                        qw = getattr(q, "w", None)
                        if qw is None:
                            # indexable (w,x,y,z)
                            qw, qx, qy, qz = float(q[0]), float(q[1]), float(q[2]), float(q[3])
                        else:
                            qx = float(getattr(q, "x", 0.0))
                            qy = float(getattr(q, "y", 0.0))
                            qz = float(getattr(q, "z", 0.0))
                            qw = float(qw)
                        # yaw from quaternion
                        yaw = float(math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz)))
                except Exception:
                    pass
            try:
                stage = omni.usd.get_context().get_stage()
                prim = None
                if stage is not None:
                    # Prefer pelvis link as "base" pose. Many humanoid USDs keep the root Xform fixed
                    # while the articulated links move, which makes base.x/y look stuck at 0.
                    pelvis_path = f"{cfg.prim_path}/pelvis"
                    pelvis_prim = stage.GetPrimAtPath(pelvis_path)
                    if pelvis_prim and pelvis_prim.IsValid():
                        prim = pelvis_prim
                    else:
                        prim = stage.GetPrimAtPath(cfg.prim_path)
                if prim and prim.IsValid():
                    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0.0)
                    p = m.ExtractTranslation()
                    rx, ry, rz = float(p[0]), float(p[1]), float(p[2])
                    # Estimate yaw from world rotation matrix
                    r = m.ExtractRotationMatrix()
                    yaw = float(math.atan2(r[1][0], r[0][0]))
            except Exception:
                pass

        # Keep FPV camera "attached" to humanoid base pose (USD hierarchy doesn't always move with physics).
        self._update_fpv_mount(rx, ry, rz, yaw)

        # If an external mode override was requested via websocket, use it.
        mode = cfg.mode
        if self._mode_override in ("goal", "external"):
            mode = str(self._mode_override)

        # Compute command (vx, vy, wz) for *all* variants so non-H1 can still be driven.
        if mode == "external":
            now = time.time()
            with self._lock:
                age = now - float(self._latest_cmd_t)
                cmd = self._latest_cmd.copy()
            if age > float(cfg.cmd_timeout_s):
                cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        else:
            gx, gy = self._goal_xy()
            dx = gx - rx
            dy = gy - ry
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= cfg.stop_radius:
                cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
            else:
                desired_yaw = float(np.arctan2(dy, dx))
                yaw_err = desired_yaw - yaw
                # wrap to [-pi, pi]
                yaw_err = (yaw_err + np.pi) % (2 * np.pi) - np.pi
                wz = float(np.clip(cfg.wz_gain * yaw_err, -cfg.wz_max, cfg.wz_max))
                cmd = np.array([cfg.vx, 0.0, wz], dtype=np.float32)

        did_apply_locomotion = False

        # Apply command via policy controller when available.
        if self._h1 is not None and self._using_h1_policy:
            try:
                self._h1.forward(step_size, cmd)
                did_apply_locomotion = True
            except Exception:
                pass

        # Fallback: drive base rigid body velocities (navigation-only, not real walking).
        if (not self._using_h1_policy) and self._dc is not None and self._dc_base_body is not None:
            # Also drive a simple joint-space gait for visual walking (best-effort).
            try:
                self._apply_simple_gait(cmd, step_size)
            except Exception:
                pass
            try:
                # cmd is in the robot's forward frame; convert to world using yaw.
                cy = math.cos(yaw)
                sy = math.sin(yaw)
                vxw = float(cy * cmd[0] - sy * cmd[1])
                vyw = float(sy * cmd[0] + cy * cmd[1])
                self._dc.set_rigid_body_linear_velocity(self._dc_base_body, (vxw, vyw, 0.0))
                self._dc.set_rigid_body_angular_velocity(self._dc_base_body, (0.0, 0.0, float(cmd[2])))
                did_apply_locomotion = True
                # Breadcrumb: confirm we are attempting to apply velocities (rate-limited; warn-level).
                try:
                    now2 = time.time()
                    if (abs(float(cmd[0])) + abs(float(cmd[1])) + abs(float(cmd[2]))) > 1e-4 and (
                        (now2 - float(getattr(self, "_dbg_last_warn_t", 0.0))) >= 1.0
                    ):
                        self._dbg_last_warn_t = now2
                        carb.log_warn(
                            f"[gil.h1_maze_walker] dc_apply base={getattr(self, '_dc_base_path', '')!s} "
                            f"cmd_robot=({float(cmd[0]):.3f},{float(cmd[1]):.3f},{float(cmd[2]):.3f}) "
                            f"cmd_world=({vxw:.3f},{vyw:.3f},{float(cmd[2]):.3f})"
                        )
                except Exception:
                    pass
            except Exception:
                pass

        # If we have non-zero cmd but couldn't apply locomotion via any backend, warn occasionally.
        if not did_apply_locomotion:
            try:
                now3 = time.time()
                if (abs(float(cmd[0])) + abs(float(cmd[1])) + abs(float(cmd[2]))) > 1e-4 and (
                    (now3 - float(getattr(self, "_dbg_last_warn_t", 0.0))) >= 1.0
                ):
                    self._dbg_last_warn_t = now3
                    carb.log_warn(
                        "[gil.h1_maze_walker] NO_LOCOMOTION_PATH "
                        f"using_h1_policy={bool(getattr(self, '_using_h1_policy', False))} "
                        f"dc={'yes' if getattr(self, '_dc', None) is not None else 'no'} "
                        f"dc_base={'yes' if getattr(self, '_dc_base_body', None) is not None else 'no'} "
                        f"cmd=({float(cmd[0]):.3f},{float(cmd[1]):.3f},{float(cmd[2]):.3f})"
                    )
            except Exception:
                pass

        # Cache base pose/cmd for async send loop. Avoid calling IMU/ContactSensor from within physics callback
        # as it can create/consume PhysX tensor views that get invalidated and can stall the sim.
        now = time.time()
        with self._lock:
            self._last_base_pose = (float(rx), float(ry), float(rz), float(yaw))
            self._last_cmd_cache = cmd.copy()
            self._last_pose_update_t = now

        # Publish ROS2 odometry so the ROS2-mode controls stack can observe motion.
        try:
            self._ros2_publish_odom(cfg, float(rx), float(ry), float(rz), float(yaw), cmd)
        except Exception:
            pass

        # Publish images at ~2 Hz if available
        if (now - self._last_image_send_t) >= 0.5:
            self._last_image_send_t = now
            img_main = ""
            img_wide = ""
            extra_images: dict[str, str] = {}
            try:
                if self._rgb_annot_fpv is not None:
                    img_main = self._encode_jpeg_data_url(self._rgb_annot_fpv.get_data())
                if self._rgb_annot_tpv is not None:
                    img_wide = self._encode_jpeg_data_url(self._rgb_annot_tpv.get_data())
                # Extra cameras (if enabled)
                for cp, annot in list(self._rgb_by_cam.items()):
                    try:
                        extra_images[cp] = self._encode_jpeg_data_url(annot.get_data())
                    except Exception:
                        continue
            except Exception:
                img_main = ""
                img_wide = ""
            if img_main or img_wide:
                payload = {
                    "type": "scene_image",
                    "robot_kind": "humanoid",
                    "image": img_main,
                    "image_wide": img_wide,
                    "images": extra_images,
                    "base": {"x": rx, "y": ry, "z": float(rz), "yaw": yaw},
                    "objects": {},
                    # camera_info fields can be filled later if needed
                    "camera_info": None,
                    "camera_info_wide": None,
                }
                with self._lock:
                    self._pending_image = payload

    def _build_sensors_payload_safe(self, now: float) -> dict:
        """
        Read physics sensors (IMU/contact) from the Kit/async thread (NOT inside physics callback).
        If sensors error or their PhysX tensor views get invalidated, disable briefly and recreate later.
        """
        # Global kill-switch: never read sensors when disabled.
        try:
            s = carb.settings.get_settings()
            enable = s.get("/gil/sensors/enable")
            # Default to disabled to avoid PhysX tensor view init issues during startup; users can enable explicitly.
            enable = False if enable is None else bool(enable)
            if not enable:
                return {}
        except Exception:
            return {}

        # Skip sensor reads while paused to avoid invalid simulation-view access.
        try:
            if self._timeline is None or (not self._timeline.is_playing()):
                return {}
        except Exception:
            return {}

        # Skip until a physics scene exists; otherwise sensor creation can crash/freeze PhysX tensors.
        if not self._stage_has_physics_scene():
            return {}

        if now < float(self._sensors_disabled_until_t):
            return {}

        # Lazy-import sensor wrappers only when we actually intend to create/read them.
        try:
            from isaacsim.sensors.physics.impl.imu_sensor import IMUSensor  # type: ignore
            from isaacsim.sensors.physics.impl.contact_sensor import ContactSensor  # type: ignore
        except Exception:
            return {}

        # Throttle sensor reads
        if (now - float(self._last_sensors_read_t)) < 0.1:
            return {}
        self._last_sensors_read_t = now

        sensors: dict[str, Any] = {}
        try:
            # Sensors may get attached after startup; re-check occasionally.
            if self._imu_sensor is None or not self._contact_sensors:
                self._ensure_physics_sensors_from_settings()

            if self._imu_sensor is not None:
                imu_f = self._imu_sensor.get_current_frame()
                if not isinstance(imu_f, dict) or not imu_f:
                    raise RuntimeError("imu_frame_empty")
                sensors["imu"] = {
                    "lin_acc": np.asarray(imu_f.get("lin_acc")).astype(float).tolist() if imu_f.get("lin_acc") is not None else None,
                    "ang_vel": np.asarray(imu_f.get("ang_vel")).astype(float).tolist() if imu_f.get("ang_vel") is not None else None,
                    "orientation": np.asarray(imu_f.get("orientation")).astype(float).tolist() if imu_f.get("orientation") is not None else None,
                    "time": float(imu_f.get("time", 0.0)),
                    "physics_step": float(imu_f.get("physics_step", 0.0)),
                    "prim_path": str(self._imu_sensor.prim_path),
                }

            if self._contact_sensors:
                contacts_out: dict[str, Any] = {}
                for key, cs in self._contact_sensors.items():
                    cf = cs.get_current_frame()
                    if not isinstance(cf, dict):
                        continue
                    out = {
                        "in_contact": bool(cf.get("in_contact", False)),
                        "force": float(cf.get("force", 0.0)),
                        "number_of_contacts": int(cf.get("number_of_contacts", 0)),
                        "time": float(cf.get("time", 0.0)),
                        "physics_step": float(cf.get("physics_step", 0.0)),
                        "prim_path": str(cs.prim_path),
                    }
                    if "contacts" in cf and isinstance(cf.get("contacts"), list):
                        raw = []
                        for c in cf.get("contacts", []):
                            try:
                                raw.append(
                                    {
                                        "body0": c.get("body0"),
                                        "body1": c.get("body1"),
                                        "position": np.asarray(c.get("position")).astype(float).tolist() if c.get("position") is not None else None,
                                        "normal": np.asarray(c.get("normal")).astype(float).tolist() if c.get("normal") is not None else None,
                                        "impulse": np.asarray(c.get("impulse")).astype(float).tolist() if c.get("impulse") is not None else None,
                                    }
                                )
                            except Exception:
                                continue
                        out["contacts"] = raw
                    contacts_out[key] = out
                if contacts_out:
                    sensors["contacts"] = contacts_out

            # If still empty, include a tiny debug view of expected prims so we can diagnose stage discovery.
            if not sensors:
                try:
                    s = carb.settings.get_settings()
                    dbg = s.get("/gil/sensors/debug")
                    dbg = True if dbg is None else bool(dbg)
                    if_toggle = dbg
                except Exception:
                    if_toggle = True
                if if_toggle:
                    stage = omni.usd.get_context().get_stage()

                    def _prim_info(path: str) -> dict:
                        p = None if stage is None else stage.GetPrimAtPath(path)
                        return {"path": path, "valid": bool(p and p.IsValid()), "type": (p.GetTypeName() if (p and p.IsValid()) else None)}

                    sensors["_debug"] = {
                        "imu": _prim_info("/World/Humanoid/pelvis/imu_sensor"),
                        "contacts": [
                            _prim_info("/World/Humanoid/pelvis/contact_sensor"),
                            _prim_info("/World/Humanoid/left_ankle_pitch_link/contact_sensor"),
                            _prim_info("/World/Humanoid/right_ankle_pitch_link/contact_sensor"),
                            _prim_info("/World/Humanoid/left_wrist_roll_link/contact_sensor"),
                            _prim_info("/World/Humanoid/right_wrist_roll_link/contact_sensor"),
                        ],
                    }
        except Exception as e:
            # If PhysX tensor views are invalidated, the underlying plugin can spam errors; disable briefly and recreate.
            if not self._sensors_err_logged:
                self._sensors_err_logged = True
                carb.log_warn(f"[gil.h1_maze_walker] sensors read failed; disabling briefly and recreating: {e!r}")
            self._imu_sensor = None
            self._contact_sensors = {}
            self._sensors_disabled_until_t = float(now + 1.0)
            return {}
        return sensors

    async def _ws_bridge_loop(self) -> None:
        """
        Connects Isaac Sim to the existing gil_controls websocket server (RobotControlServer).
        Receives:
        - {"type":"cmd_vel","vx":...,"vy":...,"wz":...}
        - {"type":"walker_mode","mode":"goal"|"external"}
        - {"type":"set_goal","x":...,"y":...}

        Sends:
        - {"type":"scene_state","base":{...}}
        """
        s = carb.settings.get_settings()
        uri = s.get("/gil/mcp/ws_uri")
        uri = "ws://127.0.0.1:8766" if uri is None else str(uri)

        backoff = 0.5
        while True:
            try:
                self._ensure_core_fields()
                async with websockets.connect(uri) as ws:
                    carb.log_info(f"[gil.h1_maze_walker] Connected to controls websocket: {uri}")
                    # Identify ourselves so the controls server routes commands/state correctly.
                    try:
                        await ws.send(json.dumps({"type": "hello", "kind": "humanoid"}))
                    except Exception:
                        pass
                    backoff = 0.5
                    recv_task = asyncio.create_task(self._ws_recv_loop(ws))
                    send_task = asyncio.create_task(self._ws_send_loop(ws))
                    done, pending = await asyncio.wait({recv_task, send_task}, return_when=asyncio.FIRST_EXCEPTION)
                    for t in pending:
                        t.cancel()
            except Exception as e:
                carb.log_warn(f"[gil.h1_maze_walker] Websocket bridge disconnected: {e!r}")
                await asyncio.sleep(backoff)
                backoff = min(5.0, backoff * 1.5)

    async def _ws_recv_loop(self, ws) -> None:
        self._ensure_core_fields()
        async for msg in ws:
            try:
                data = json.loads(msg)
            except Exception:
                continue
            t = str(data.get("type") or "")
            if t == "cmd_vel":
                vx = float(data.get("vx", 0.0))
                vy = float(data.get("vy", 0.0))
                wz = float(data.get("wz", 0.0))
                with self._lock:
                    self._latest_cmd = np.array([vx, vy, wz], dtype=np.float32)
                    self._latest_cmd_t = time.time()
                # Breadcrumb: confirm cmd_vel is reaching Isaac (rate-limited; warn-level for captured logs).
                try:
                    now = time.time()
                    if (now - float(getattr(self, "_dbg_last_warn_t", 0.0))) >= 1.0:
                        self._dbg_last_warn_t = now
                        carb.log_warn(
                            f"[gil.h1_maze_walker] cmd_vel rx vx={vx:.3f} vy={vy:.3f} wz={wz:.3f} "
                            f"timeline_playing={bool(self._timeline and self._timeline.is_playing())} "
                            f"mode_override={getattr(self, '_mode_override', None)!r}"
                        )
                except Exception:
                    pass
                # Safety/UX: if we receive a non-zero cmd_vel, assume the user intends "external" mode.
                # Some controls stacks only send cmd_vel (no explicit walker_mode), which would otherwise
                # leave us in default "goal" mode and result in no motion while paused.
                try:
                    if abs(vx) + abs(vy) + abs(wz) > 1e-6:
                        if self._mode_override not in ("goal", "external"):
                            self._mode_override = "external"
                        elif self._mode_override == "goal":
                            self._mode_override = "external"
                except Exception:
                    pass
                # Best-effort: ensure physics steps are running so motion + sensors update.
                try:
                    if self._timeline is not None and (not self._timeline.is_playing()):
                        carb.log_info("[gil.h1_maze_walker] cmd_vel received while paused: starting timeline")
                        self._timeline.play()
                except Exception:
                    pass
                continue
            if t == "walker_mode":
                m = str(data.get("mode") or "").lower().strip()
                if m in ("goal", "external"):
                    self._mode_override = m
                    # Best-effort: if the user explicitly sets a mode, start the timeline so physics
                    # callbacks and sensors come alive without manual Play.
                    try:
                        if self._timeline is not None and (not self._timeline.is_playing()):
                            carb.log_info(f"[gil.h1_maze_walker] walker_mode={m} received while paused: starting timeline")
                            self._timeline.play()
                    except Exception:
                        pass
                continue
            if t == "set_goal":
                try:
                    gx = float(data.get("x"))
                    gy = float(data.get("y"))
                    s = carb.settings.get_settings()
                    s.set("/gil/task/goal_x", gx)
                    s.set("/gil/task/goal_y", gy)
                except Exception:
                    pass
                continue
            if t == "reset_episode":
                # Best-effort: reset is applied inside physics step so it stays in sync with the sim.
                try:
                    req = {"x": float(data.get("x", 0.0)), "y": float(data.get("y", 0.0)), "yaw": float(data.get("yaw", 0.0))}
                except Exception:
                    req = {"x": 0.0, "y": 0.0, "yaw": 0.0}
                with self._lock:
                    self._pending_reset = req
                continue
            # Ignore unrelated commands (arm/gripper/etc.)

    async def _ws_send_loop(self, ws) -> None:
        self._ensure_core_fields()
        lock = getattr(self, "_lock", None)
        if lock is None:
            lock = Lock()
            self._lock = lock
        while True:
            payload = None
            with lock:
                if self._pending_state is not None:
                    payload = self._pending_state
                    self._pending_state = None
                elif self._pending_image is not None:
                    payload = self._pending_image
                    self._pending_image = None

            # If no physics-driven payload is pending, still publish images at low rate (works while paused).
            now = time.time()
            if payload is None and (now - self._last_image_send_t) >= 0.75:
                self._last_image_send_t = now
                img_main = ""
                img_wide = ""
                try:
                    import numpy as _np

                    async def _grab(annot, rp_path: str | None):
                        if annot is None:
                            return None
                        if not rp_path:
                            return None
                        # Try a few times to get a non-empty buffer.
                        for _ in range(5):
                            try:
                                # This is the reliable way IsaacSim's own camera tests advance render products.
                                import omni.syntheticdata.sensors as sds

                                await sds.next_render_simulation_async(rp_path, 1)
                            except Exception:
                                await asyncio.sleep(0.02)
                            try:
                                d = annot.get_data()
                            except Exception:
                                d = None
                            if d is None:
                                continue
                            a = _np.asarray(d.get("data") if isinstance(d, dict) and "data" in d else d)
                            if a.size > 0:
                                return d
                        return None

                    raw_fpv = await _grab(self._rgb_annot_fpv, self._rp_fpv_path)
                    raw_tpv = await _grab(self._rgb_annot_tpv, self._rp_tpv_path)
                    if raw_fpv is not None:
                        img_main = self._encode_jpeg_data_url(raw_fpv)
                    if raw_tpv is not None:
                        img_wide = self._encode_jpeg_data_url(raw_tpv)

                    if not self._img_debug_logged:
                        self._img_debug_logged = True
                        def _summ(x):
                            if x is None:
                                return {"type": None}
                            if isinstance(x, dict):
                                keys = list(x.keys())[:12]
                                v = x.get("data") if "data" in x else None
                                if v is not None:
                                    a = _np.asarray(v)
                                    return {"type": "dict", "keys": keys, "data_shape": list(a.shape), "data_dtype": str(a.dtype)}
                                return {"type": "dict", "keys": keys}
                            a = _np.asarray(x)
                            return {"type": str(type(x)), "shape": list(a.shape), "dtype": str(a.dtype)}

                        carb.log_info(f"[gil.h1_maze_walker] rgb_fpv={_summ(raw_fpv)} enc_len={len(img_main) if img_main else 0}")
                        carb.log_info(f"[gil.h1_maze_walker] rgb_tpv={_summ(raw_tpv)} enc_len={len(img_wide) if img_wide else 0}")
                except Exception:
                    img_main = ""
                    img_wide = ""
                if img_main or img_wide:
                    payload = {
                        "type": "scene_image",
                        "robot_kind": "humanoid",
                        "image": img_main,
                        "image_wide": img_wide,
                        "objects": {},
                        "camera_info": None,
                        "camera_info_wide": None,
                    }

            # Also publish a heartbeat scene_state occasionally so the controls server can switch active kind.
            if payload is None and (now - self._last_state_send_t) >= 0.1:
                self._last_state_send_t = now
                # Read base pose from USD every heartbeat (prefer pelvis), so even if physics callbacks
                # are stalled or the root prim stays fixed, we still report motion correctly.
                bx = by = bz = byaw = 0.0
                try:
                    cfg = self._read_cfg()
                    stage = omni.usd.get_context().get_stage()
                    prim = None
                    if stage is not None:
                        pelvis = stage.GetPrimAtPath(f"{cfg.prim_path}/pelvis")
                        prim = pelvis if (pelvis and pelvis.IsValid()) else stage.GetPrimAtPath(cfg.prim_path)
                    if prim and prim.IsValid():
                        m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0.0)
                        p = m.ExtractTranslation()
                        bx, by, bz = float(p[0]), float(p[1]), float(p[2])
                        r = m.ExtractRotationMatrix()
                        byaw = float(math.atan2(r[1][0], r[0][0]))
                except Exception:
                    pass
                with self._lock:
                    self._last_base_pose = (float(bx), float(by), float(bz), float(byaw))
                    cmd = self._last_cmd_cache.copy()
                sensors = self._build_sensors_payload_safe(now)
                payload = {
                    "type": "scene_state",
                    "robot_kind": "humanoid",
                    "base": {"x": float(bx), "y": float(by), "z": float(bz), "yaw": float(byaw), "vx": float(cmd[0]), "vy": float(cmd[1]), "wz": float(cmd[2])},
                    "objects": {},
                    "sensors": sensors,
                }

            if payload is not None:
                try:
                    await ws.send(json.dumps(payload))
                except Exception:
                    return

            # Kinematic fallback:
            # - When paused: always allow a USD-transform move so motion is visible.
            # - When playing: if cmd_vel is non-zero but the reported base pose isn't changing (stall),
            #   also apply the USD-transform move as a last-resort fallback.
            try:
                if self._timeline is not None:
                    s = carb.settings.get_settings()
                    drive_when_paused = s.get("/gil/walker/drive_when_paused")
                    drive_when_paused = True if drive_when_paused is None else bool(drive_when_paused)

                    # Check if we should "unstick" motion while playing.
                    do_stall_fallback = False
                    with self._lock:
                        base_now = self._last_base_pose
                        cmd_now = self._latest_cmd.copy()
                        cmd_t = float(self._latest_cmd_t)
                    # Consider cmd stale if we haven't received an update recently.
                    if (now - cmd_t) > 0.8:
                        cmd_now = np.array([0.0, 0.0, 0.0], dtype=np.float32)
                    speed = float(abs(cmd_now[0]) + abs(cmd_now[1]) + abs(cmd_now[2]))
                    if speed > 1e-4:
                        # For stall detection, prefer the root prim transform (pelvis can oscillate in-place and
                        # hide "no translation" situations).
                        try:
                            cfg2 = self._read_cfg()
                            stage2 = omni.usd.get_context().get_stage()
                            prim2 = None if stage2 is None else stage2.GetPrimAtPath(str(cfg2.prim_path))
                            if prim2 and prim2.IsValid():
                                m2 = UsdGeom.Xformable(prim2).ComputeLocalToWorldTransform(0.0)
                                p2 = m2.ExtractTranslation()
                                r2 = m2.ExtractRotationMatrix()
                                base_now = (float(p2[0]), float(p2[1]), float(p2[2]), float(math.atan2(r2[1][0], r2[0][0])))
                        except Exception:
                            pass
                        if self._stall_check_pose is None:
                            self._stall_check_pose = base_now
                            self._stall_check_t = now
                        elif (now - float(self._stall_check_t)) >= 0.4:
                            bx0, by0, bz0, byaw0 = self._stall_check_pose
                            bx1, by1, bz1, byaw1 = base_now
                            # Treat as stalled if we haven't moved meaningfully in ~0.4s.
                            if (abs(bx1 - bx0) + abs(by1 - by0) + abs(byaw1 - byaw0)) < 1e-4:
                                do_stall_fallback = True
                            self._stall_check_pose = base_now
                            self._stall_check_t = now
                    else:
                        self._stall_check_pose = None
                        self._stall_check_t = now

                    if (not self._timeline.is_playing()):
                        if drive_when_paused:
                            self._tick_kinematic_when_paused()
                    else:
                        # Avoid "mannequin sliding" when physics is running: only allow the stall kinematic fallback
                        # if explicitly enabled. However, when we *can't* drive locomotion via policy/dynamic-control
                        # (e.g. articulation discovery fails), enable this automatically so cmd_vel still moves the base.
                        allow_stall_kin = s.get("/gil/walker/allow_stall_kinematic_fallback")
                        # Default: ON (to keep external cmd_vel usable) unless the user explicitly set it.
                        allow_stall_kin = True if allow_stall_kin is None else bool(allow_stall_kin)
                        # If the user requested "real locomotion only", never engage kinematic fallback.
                        try:
                            if bool(getattr(cfg, "require_policy", False)):
                                allow_stall_kin = False
                        except Exception:
                            pass
                        # If the user explicitly disabled it, still allow it when we have no other locomotion path.
                        no_other_locomotion = (not bool(getattr(self, "_using_h1_policy", False))) and (
                            (getattr(self, "_dc", None) is None) or (getattr(self, "_dc_base_body", None) is None)
                        )
                        if do_stall_fallback and (allow_stall_kin or no_other_locomotion):
                            # Breadcrumb (warn-level) so we can confirm this is triggering.
                            try:
                                if (now - float(getattr(self, "_dbg_last_warn_t", 0.0))) >= 1.0:
                                    self._dbg_last_warn_t = now
                                    carb.log_warn(
                                        f"[gil.h1_maze_walker] stall_kinematic_fallback engaged "
                                        f"allow={bool(allow_stall_kin)} no_other={bool(no_other_locomotion)}"
                                    )
                            except Exception:
                                pass
                            self._tick_kinematic_when_paused()
            except Exception:
                pass
            await asyncio.sleep(0.05)

    def _update_fpv_mount(self, rx: float, ry: float, rz: float, yaw: float) -> None:
        """Best-effort: keep synthetic FPV camera aligned to base pose. Safe to call even while paused."""
        now = time.time()
        if (now - self._last_fpv_update_t) < (1.0 / 30.0):
            return
        self._last_fpv_update_t = now
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            s = carb.settings.get_settings()
            cam_path = s.get("/gil/camera/fpv_prim_path")
            cam_path = "/World/Humanoid/FPVCamera" if cam_path is None else str(cam_path)
            cam_prim = stage.GetPrimAtPath(cam_path)
            # Only override pose for our synthetic FPV camera.
            if not (cam_path == "/World/Humanoid/FPVCamera" and cam_prim and cam_prim.IsValid()):
                return

            ox = s.get("/gil/camera/fpv_x")
            oy = s.get("/gil/camera/fpv_y")
            oz = s.get("/gil/camera/fpv_z")
            ox = 0.20 if ox is None else float(ox)
            oy = 0.00 if oy is None else float(oy)
            oz = 1.55 if oz is None else float(oz)

            cy = math.cos(yaw)
            sy = math.sin(yaw)
            wx = rx + (cy * ox - sy * oy)
            wy = ry + (sy * ox + cy * oy)
            wz = float(rz) + oz

            xf = UsdGeom.Xformable(cam_prim)
            ops = xf.GetOrderedXformOps()
            xlate = None
            rot = None
            for op in ops:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    xlate = op
                if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
                    rot = op
            if xlate is None:
                xlate = xf.AddTranslateOp()
            if rot is None:
                rot = xf.AddRotateXYZOp()

            pitch = s.get("/gil/camera/fpv_pitch")
            yaw_off = s.get("/gil/camera/fpv_yaw")
            roll_off = s.get("/gil/camera/fpv_roll")
            pitch = 0.0 if pitch is None else float(pitch)
            yaw_off = -90.0 if yaw_off is None else float(yaw_off)
            roll_off = 0.0 if roll_off is None else float(roll_off)
            yaw_deg = float(yaw * 180.0 / math.pi)
            xlate.Set((wx, wy, wz))
            rot.Set((pitch, yaw_off, yaw_deg + roll_off))
        except Exception:
            return

    def _tick_kinematic_when_paused(self) -> None:
        """
        When timeline isn't playing, integrate cmd_vel and author a USD transform on the robot prim.
        This is a visual/navigation fallback so users can see motion even before physics steps.
        """
        now = time.time()
        dt = float(now - float(self._last_kin_update_t))
        if dt <= 0.0:
            return
        # Clamp dt to avoid big jumps after stalls.
        dt = float(min(0.1, dt))
        self._last_kin_update_t = now

        cfg = self._read_cfg()
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(cfg.prim_path)
        if not (prim and prim.IsValid()):
            return

        # Read current base pose from USD.
        try:
            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0.0)
            p = m.ExtractTranslation()
            rx, ry, rz = float(p[0]), float(p[1]), float(p[2])
            r = m.ExtractRotationMatrix()
            yaw = float(math.atan2(r[1][0], r[0][0]))
        except Exception:
            return

        # Determine active mode.
        mode = cfg.mode
        if self._mode_override in ("goal", "external"):
            mode = str(self._mode_override)

        # Compute command.
        if mode == "external":
            with self._lock:
                age = now - float(self._latest_cmd_t)
                cmd = self._latest_cmd.copy()
            if age > float(cfg.cmd_timeout_s):
                cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        else:
            gx, gy = self._goal_xy()
            dx = gx - rx
            dy = gy - ry
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= cfg.stop_radius:
                cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
            else:
                desired_yaw = float(np.arctan2(dy, dx))
                yaw_err = desired_yaw - yaw
                yaw_err = (yaw_err + np.pi) % (2 * np.pi) - np.pi
                wz = float(np.clip(cfg.wz_gain * yaw_err, -cfg.wz_max, cfg.wz_max))
                cmd = np.array([cfg.vx, 0.0, wz], dtype=np.float32)

        # Integrate pose (cmd in robot frame).
        try:
            cy = math.cos(yaw)
            sy = math.sin(yaw)
            vxw = float(cy * cmd[0] - sy * cmd[1])
            vyw = float(sy * cmd[0] + cy * cmd[1])
            rx += vxw * dt
            ry += vyw * dt
            yaw += float(cmd[2]) * dt
        except Exception:
            return

        # Author USD transform.
        try:
            xf = UsdGeom.Xformable(prim)
            ops = xf.GetOrderedXformOps()
            xlate = None
            rot = None
            for op in ops:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    xlate = op
                if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
                    rot = op
            if xlate is None:
                xlate = xf.AddTranslateOp()
            if rot is None:
                rot = xf.AddRotateXYZOp()
            xlate.Set((float(rx), float(ry), float(rz)))
            rot.Set((0.0, 0.0, float(yaw * 180.0 / math.pi)))
        except Exception:
            return

        # Cache pose/cmd so websocket scene_state reports motion even while paused.
        try:
            with self._lock:
                self._last_base_pose = (float(rx), float(ry), float(rz), float(yaw))
                self._last_cmd_cache = cmd.copy()
                self._last_pose_update_t = float(now)
        except Exception:
            pass

        # Keep FPV camera aligned too.
        self._update_fpv_mount(float(rx), float(ry), float(rz), float(yaw))

    def on_shutdown(self) -> None:
        carb.log_info("[gil.h1_maze_walker] shutdown")
        try:
            if self._world is not None and bool(getattr(self, "_physics_cb_registered", False)):
                self._world.remove_physics_callback("gil_h1_maze_walker_step")
        except Exception:
            pass
        self._physics_cb_registered = False
        # Clear Isaac Core singleton world/sim-context to avoid reusing invalidated PhysX tensor views.
        try:
            World.clear_instance()  # type: ignore[attr-defined]
        except Exception:
            pass
        self._world = None
        self._imu_sensor = None
        self._contact_sensors = {}
        # Stop ROS2 node/thread (best-effort).
        try:
            self._ros2_stop.set()
        except Exception:
            pass
        try:
            if self._ros2_node is not None:
                try:
                    self._ros2_node.destroy_node()
                except Exception:
                    pass
        except Exception:
            pass
        self._ros2_node = None
        self._ros2_pub_odom = None
        self._ros2_sub_cmd_vel = None
        self._ros2_sub_mode = None
        self._ros2_started = False
        try:
            if self._ws_task is not None:
                self._ws_task.cancel()
        except Exception:
            pass
        try:
            if self._cam_task is not None:
                self._cam_task.cancel()
        except Exception:
            pass
        try:
            if getattr(self, "_task", None) is not None:
                self._task.cancel()
        except Exception:
            pass


