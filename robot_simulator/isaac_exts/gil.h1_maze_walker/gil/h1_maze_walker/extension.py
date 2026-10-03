import asyncio
import datetime
import json
import math
import os
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
try:
    from gil.h1_maze_walker.g1_policy import G1FlatTerrainPolicy
except Exception:  # pragma: no cover - Isaac Sim environment specific
    G1FlatTerrainPolicy = None  # type: ignore[assignment]
import base64
from io import BytesIO
from pxr import Sdf, Usd, UsdGeom, UsdPhysics


def _carb_bool(value: Any, default: bool = False) -> bool:
    """Carb often stores flags as the strings 'true'/'false'; bool('false') is True."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")
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
    # If the yaw error is large, rotate in place first (prevents wide arcs that can leave tight mazes).
    turn_in_place_err: float = 0.8
    # Minimum forward speed scale while turning sharply. Some locomotion policies don't rotate well at vx=0.
    turn_min_vx_scale: float = 0.12

    # Stop condition near goal
    stop_radius: float = 0.6

    # Control mode:
    # - "goal": steer toward /gil/task/goal_{x,y}
    # - "external": use latest cmd_vel received from websocket (MCP)
    mode: str = "goal"
    cmd_timeout_s: float = 0.6

    # Safety: if base height drops below this while physics is stepping, zero commands.
    min_upright_base_z_m: float = 0.55

    # Policy scaling: H1 policy behaves as if wz is deg/s; additionally scale it down for stability.
    h1_wz_scale: float = 0.35

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

        # Fields used by websocket send/recv loops (can survive hot-reload via running tasks).
        # Ensure they exist so background tasks don't crash with AttributeError.
        try:
            getattr(self, "_pending_state")
        except Exception:
            self._pending_state = None
        try:
            getattr(self, "_pending_image")
        except Exception:
            self._pending_image = None
        try:
            getattr(self, "_pending_reset")
        except Exception:
            self._pending_reset = None
        try:
            getattr(self, "_last_state_send_t")
        except Exception:
            self._last_state_send_t = 0.0
        try:
            getattr(self, "_last_image_send_t")
        except Exception:
            self._last_image_send_t = 0.0

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

        # 2) Resolve base rigid body: prefer pelvis/torso/root rigid-body prims (assets vary).
        base_candidates: list[str] = []
        for prefix in [root_prim_path, art_path]:
            if prefix:
                base_candidates.append(f"{prefix}/pelvis")
                base_candidates.append(f"{prefix}/Pelvis")
                base_candidates.append(f"{prefix}/pelvis/torso")
                base_candidates.append(f"{prefix}/Pelvis/torso")
                base_candidates.append(f"{prefix}/torso")
                base_candidates.append(f"{prefix}/Torso")
                base_candidates.append(f"{prefix}/base")
                base_candidates.append(f"{prefix}/base_link")
                base_candidates.append(f"{prefix}/Base")
                base_candidates.append(f"{prefix}/BaseLink")
        try:
            root = stage.GetPrimAtPath(root_prim_path)
            if root and root.IsValid():
                for p in self._prim_range(root):
                    try:
                        nm = (p.GetName() or "").lower()
                        if nm in ("pelvis", "torso", "base", "base_link", "root", "root_link"):
                            # Prefer rigid-body prims if we can detect them via USD APIs.
                            has_rb = False
                            try:
                                has_rb = bool(p.HasAPI(UsdPhysics.RigidBodyAPI))
                            except Exception:
                                has_rb = False
                            if not has_rb:
                                try:
                                    if PhysxSchema is not None:
                                        has_rb = bool(p.HasAPI(PhysxSchema.PhysxRigidBodyAPI))
                                except Exception:
                                    has_rb = False
                            if has_rb:
                                base_candidates.append(str(p.GetPath()))
                                # Some assets put the rigid-body API on a child prim; include children too.
                                try:
                                    for c in p.GetChildren():
                                        cn = (c.GetName() or "").lower()
                                        if cn in ("pelvis", "torso", "base", "base_link", "root", "root_link"):
                                            base_candidates.append(str(c.GetPath()))
                                except Exception:
                                    pass
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
            # Final resort: pick a rigid body under the humanoid prefix, preferring names that look like the trunk.
            try:
                root = stage.GetPrimAtPath(root_prim_path)
                if root and root.IsValid():
                    best_path = ""
                    best_handle = None
                    best_score = -10_000
                    probed = 0
                    for p in self._prim_range(root):
                        if probed >= 1200:
                            break
                        probed += 1
                        try:
                            cand = str(p.GetPath())
                            h = dc.get_rigid_body(cand)
                            if h:
                                s = cand.lower()
                                nm = (p.GetName() or "").lower()
                                score = 0
                                # Prefer trunk bodies (pelvis/torso) heavily.
                                if ("pelvis" in s) or ("pelvis" in nm):
                                    score += 1000
                                if ("torso" in s) or ("trunk" in s) or ("torso" in nm) or ("trunk" in nm):
                                    score += 700
                                if ("base" in s) or ("root" in s) or ("base" in nm) or ("root" in nm):
                                    score += 300
                                # De-prefer feet/ankles so we don't "reset" a foot link.
                                if any(bad in s for bad in ("ankle", "foot", "toe")) or any(
                                    bad in nm for bad in ("ankle", "foot", "toe")
                                ):
                                    score -= 800

                                if best_handle is None or score > best_score:
                                    best_score = score
                                    best_path = cand
                                    best_handle = h
                                    if best_score >= 1000:
                                        break
                        except Exception:
                            continue
                    if best_handle is not None:
                        base_path = best_path
                        base_handle = best_handle
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
        # Cache last good physics-derived base pose so transient read failures don't snap us back to USD root pose.
        self._last_good_base_pose: dict[str, float] | None = None
        self._rgb_annot_fpv = None
        self._rgb_annot_tpv = None
        self._cam_task: asyncio.Task | None = None
        self._gt_task: asyncio.Task | None = None
        self._rep = None
        self._img_debug_logged = False
        self._rp_fpv_path: str | None = None
        self._rp_tpv_path: str | None = None
        self._rp_by_cam: dict[str, str] = {}
        self._rgb_by_cam: dict[str, Any] = {}
        self._last_extra_cam_scan_t = 0.0
        self._capture_configured = False
        self._gt_scene = None
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
            try:
                if bool(carb.settings.get_settings().get("/gil/physics_scene_ready")):
                    return True
            except Exception:
                pass
            for path in ("/physicsScene", "/World/physicsScene"):
                prim = None
                try:
                    prim = stage.GetPrimAtPath(Sdf.Path(path))
                except Exception:
                    try:
                        prim = stage.GetPrimAtPath(path)
                    except Exception:
                        prim = None
                if prim is None:
                    continue
                try:
                    if not prim.IsValid():
                        continue
                except Exception:
                    continue
                return True
            for p in Usd.PrimRange(stage.GetPseudoRoot()):
                try:
                    if str(p.GetName() or "") == "physicsScene":
                        return True
                except Exception:
                    continue
            for p in Usd.PrimRange(stage.GetPseudoRoot()):
                try:
                    if p.IsA(UsdPhysics.Scene):
                        return True
                except Exception:
                    continue
                try:
                    if "PhysicsScene" in str(p.GetTypeName() or ""):
                        return True
                except Exception:
                    continue
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

        # Replicator/SyntheticData annotators can return EMPTY buffers when the Fabric Scene Delegate
        # is enabled (common symptom: "SdRenderVarPtr missing valid input renderVar LdrColorSDhost").
        # For camera/GT capture workflows we prefer correctness over Fabric performance.
        try:
            s = carb.settings.get_settings()
            fabric = s.get("/app/useFabricSceneDelegate")
            if fabric is None:
                fabric = False
            if bool(fabric):
                s.set("/app/useFabricSceneDelegate", False)
                carb.log_warn("[gil.h1_maze_walker] Disabled Fabric Scene Delegate for Replicator capture compatibility (/app/useFabricSceneDelegate=false)")
        except Exception:
            pass

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
        # IMPORTANT: Do not start the timeline here.
        #
        # Isaac's `SimulationManager` creates tensors SimulationView in response to Physics warmup,
        # which is triggered on timeline PLAY. If we play the timeline before a PhysicsScene exists
        # and before assets finish loading, we can get:
        #   "Failed to create simulation view: no active physics scene found"
        #
        # Instead, we only start PLAY *after* World/sim-context init succeeds (see post-init block).
        auto_play = False
        try:
            s = carb.settings.get_settings()
            v = s.get("/gil/humanoid/auto_play")
            auto_play = False if v is None else bool(v)
        except Exception as e:
            carb.log_warn(f"[gil.h1_maze_walker] auto_play check failed: {e!r}")

        # Start websocket bridge ASAP so controls MCP can see a humanoid client even before Play.
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.ensure_future(self._ws_bridge_loop())

        # Setup camera capture ASAP (works even if paused; best-effort).
        # Replicator/cameras can come up late, so we retry until it succeeds.
        try:
            s = carb.settings.get_settings()
            cap = s.get("/gil/camera/capture_enabled")
            cap = True if cap is None else bool(cap)
        except Exception:
            cap = True
        if cap:
            if self._cam_task is None or self._cam_task.done():
                self._cam_task = asyncio.ensure_future(self._camera_capture_retry_loop())
        else:
            # If capture is disabled, avoid creating Replicator render products.
            self._cam_task = None

        # Start GT export loop even if websocket bridge isn't connected.
        try:
            s = carb.settings.get_settings()
            gt_enabled = s.get("/gil/gt_scene/enabled")
            gt_enabled = False if gt_enabled is None else bool(gt_enabled)
        except Exception:
            gt_enabled = False
        if gt_enabled:
            if self._gt_task is None or self._gt_task.done():
                self._gt_task = asyncio.ensure_future(self._gt_scene_loop())

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
        for _ in range(3600):  # ~60s at 60 Hz; maze_env may define the scene after we start waiting
            if self._stage_has_physics_scene():
                physics_ready = True
                break
            await app.next_update_async()
        if not physics_ready:
            carb.log_warn("[gil.h1_maze_walker] No UsdPhysics.Scene detected yet; skipping World/sim-context init to avoid PhysX tensors errors.")
            return

        # The PhysicsScene USD prim can appear a few frames before PhysX has fully created/activated
        # the corresponding runtime scene. If we initialize tensors SimulationView too early, Isaac
        # can error with "no active physics scene found". Give PhysX some time here.
        for _ in range(60):  # ~1s at 60Hz
            await app.next_update_async()

        try:
            World.clear_instance()  # type: ignore[attr-defined]
        except Exception:
            pass
        # Isaac may still be wiring up PhysX/tensors even after a UsdPhysics.Scene appears.
        # Initializing the SimulationContext can fail transiently with:
        #   "Failed to create simulation view: no active physics scene found"
        #
        # IMPORTANT: `World.reset_async()` calls `SimulationContext.play_async()`, which expects a
        # `PhysicsContext` to exist. That context is created by `initialize_simulation_context_async()`.
        # If we skip initialization and jump straight to reset_async, Isaac can raise:
        #   AttributeError("'NoneType' object has no attribute 'warm_start'")
        #
        # So we do: initialize_simulation_context_async() -> reset_async(), with retries.
        last_world_err = None
        for attempt in range(180):  # ~3s at 60Hz
            try:
                # Give Kit a few ticks after the physics scene appears.
                # Without this, we can race PhysX scene attachment and wedge tensors SimulationView.
                if attempt == 0:
                    for _ in range(3):
                        await app.next_update_async()
                self._world = World(stage_units_in_meters=1.0, physics_dt=1 / 200, rendering_dt=2 / 200)
                await app.next_update_async()
                await self._world.initialize_simulation_context_async()
                await app.next_update_async()
                await self._world.reset_async()
                await app.next_update_async()
                last_world_err = None
                break
            except Exception as e:
                last_world_err = e
                try:
                    self._world = None
                    World.clear_instance()  # type: ignore[attr-defined]
                except Exception:
                    pass
                if attempt in (0, 59, 119, 179):
                    carb.log_warn(f"[gil.h1_maze_walker] World/sim-context init retry {attempt+1}/180 failed: {e!r}")
                await app.next_update_async()
        if last_world_err is not None:
            carb.log_warn(f"[gil.h1_maze_walker] World/sim-context init failed: {last_world_err!r}")
            return

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
        want_g1_policy = str(cfg.variant).lower().strip() in ("g1", "unitree_g1")
        if want_g1_policy and G1FlatTerrainPolicy is not None:
            last_err = None
            # PolicyController.__init__ re-binds the USD. Retrying hundreds of times with no
            # Nucleus .pt looks like zero-G teleports, then a ragdoll. Try a few times only.
            for attempt in range(3):
                try:
                    sx, sy = self._maze_spawn_xy()
                    sz = self._standing_base_z()
                    self._h1 = G1FlatTerrainPolicy(
                        prim_path=cfg.prim_path,
                        name="humanoid_g1",
                        usd_path=None,
                        position=np.array([sx, sy, sz], dtype=np.float32),
                    )
                    await app.next_update_async()
                    self._h1.initialize()
                    await app.next_update_async()
                    self._using_h1_policy = True
                    carb.log_warn(
                        f"[gil.h1_maze_walker] locomotion_controller=G1FlatTerrainPolicy prim={cfg.prim_path} "
                        f"attempt={attempt+1}"
                    )
                    break
                except Exception as e:
                    last_err = e
                    self._h1 = None
                    self._using_h1_policy = False
                    carb.log_warn(
                        f"[gil.h1_maze_walker] G1FlatTerrainPolicy init retry {attempt+1}/3 failed: {e!r}"
                    )
                    await app.next_update_async()
            if not self._using_h1_policy and last_err is not None:
                carb.log_warn(f"[gil.h1_maze_walker] G1FlatTerrainPolicy init failed: {last_err!r}")
        elif want_g1_policy and G1FlatTerrainPolicy is None:
            carb.log_warn("[gil.h1_maze_walker] G1FlatTerrainPolicy module not available")

        want_h1_policy = ((cfg.variant == "h1") or bool(cfg.force_h1_policy)) and (not want_g1_policy)
        if want_h1_policy and H1FlatTerrainPolicy is not None:
            # Isaac can report "no active physics scene found" during early startup; the policy init
            # may fail until PhysX/tensors are fully attached. Retry briefly.
            last_err = None
            for attempt in range(240):  # ~4s at 60Hz (best-effort)
                try:
                    sx, sy = self._maze_spawn_xy()
                    sz = self._standing_base_z()
                    self._h1 = H1FlatTerrainPolicy(
                        prim_path=cfg.prim_path,
                        name="humanoid_h1",
                        position=np.array([sx, sy, sz], dtype=np.float32),
                    )
                    await app.next_update_async()
                    self._h1.initialize()
                    await app.next_update_async()
                    try:
                        cy = 1.0
                        syaw = 0.0
                        self._h1.robot.set_world_pose(
                            np.array([sx, sy, sz], dtype=np.float32),
                            np.array([cy, 0.0, 0.0, syaw], dtype=np.float32),
                        )
                    except Exception:
                        pass
                    self._using_h1_policy = True
                    carb.log_warn(
                        f"[gil.h1_maze_walker] locomotion_controller=H1FlatTerrainPolicy prim={cfg.prim_path} "
                        f"variant={cfg.variant} attempt={attempt+1}"
                    )
                    break
                except Exception as e:
                    last_err = e
                    self._h1 = None
                    self._using_h1_policy = False
                    # Log a breadcrumb at low rate so captured logs show progress without spamming.
                    if attempt in (0, 59, 119, 179, 239):
                        carb.log_warn(
                            f"[gil.h1_maze_walker] H1FlatTerrainPolicy init retry {attempt+1}/240 failed: {e!r}"
                        )
                    await app.next_update_async()
            if not self._using_h1_policy and last_err is not None:
                carb.log_warn(f"[gil.h1_maze_walker] H1FlatTerrainPolicy init failed (variant={cfg.variant}): {last_err!r}")
        else:
            if want_h1_policy and H1FlatTerrainPolicy is None:
                carb.log_warn("[gil.h1_maze_walker] H1FlatTerrainPolicy not available in this Isaac Sim build")

        # Setup dynamic-control interface.
        #
        # Even when the H1 policy is active, we still want dynamic-control handles for:
        # - reliable base pose (many humanoid USDs keep the articulation root Xform fixed)
        # - reliable reset (teleporting the root Xform may not move the physical bodies)
        #
        # We only *drive* base velocities when no policy is active and fallback_base_velocity is enabled.
        self._dc = None
        self._dc_art = None
        self._dc_base_body = None
        try:
            from omni.isaac.dynamic_control import _dynamic_control  # type: ignore

            self._dc = _dynamic_control.acquire_dynamic_control_interface()
            art_path, art_h, base_path, base_h = self._resolve_dc_articulation_and_base(self._dc, str(cfg.prim_path))
            self._dc_art = art_h
            self._dc_base_body = base_h
            self._dc_art_path = str(art_path or "")
            self._dc_base_path = str(base_path or "")
            carb.log_info(
                f"[gil.h1_maze_walker] dynamic_control acquired art={self._dc_art_path or '<none>'} base={self._dc_base_path or '<none>'}"
            )
        except Exception as e:
            self._dc = None
            self._dc_art = None
            self._dc_base_body = None
            self._dc_art_path = ""
            self._dc_base_path = ""
            carb.log_warn(f"[gil.h1_maze_walker] dynamic_control init failed: {e!r}")

        # If requested and no policy is active, we will drive base velocities via dynamic-control.
        if self._using_h1_policy or (not bool(cfg.fallback_base_velocity)):
            pass
        else:
            if self._dc_base_body is None:
                carb.log_warn(f"[gil.h1_maze_walker] fallback_base_velocity requested but no rigid body found under {cfg.prim_path}")

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
            from isaacsim.sensors.physics.impl.imu_sensor import IMUSensor  # type: ignore
            from isaacsim.sensors.physics.impl.contact_sensor import ContactSensor  # type: ignore
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
        Wait for the body-mounted robot cameras (or the synthetic FPV fallback).
        Third-person is optional and is not part of the robot sensor suite.
        """
        app = omni.kit.app.get_app()
        for _ in range(int(timeout_updates)):
            try:
                stage = omni.usd.get_context().get_stage()
                s = carb.settings.get_settings()
                fpv_path = s.get("/gil/camera/fpv_prim_path")
                fpv_path = "/World/Humanoid/FPVCamera" if fpv_path is None else str(fpv_path)
                wide_needed = bool(s.get("/gil/camera/wide_enabled") or False)
                fpv = None if stage is None else stage.GetPrimAtPath(fpv_path)
                fpv_ok = bool(fpv and fpv.IsValid() and fpv.IsA(UsdGeom.Camera))
                if not fpv_ok and stage is not None:
                    root = stage.GetPrimAtPath("/World/Humanoid")
                    if root and root.IsValid():
                        for p in Usd.PrimRange(root):
                            if p.IsA(UsdGeom.Camera) and p.GetName() == "front_cam":
                                fpv_ok = True
                                break
                tpv_ok = True
                if wide_needed:
                    tpv_path = s.get("/gil/camera/tpv_prim_path")
                    tpv_path = "/World/ThirdPersonCamera" if tpv_path is None else str(tpv_path)
                    tpv = None if stage is None else stage.GetPrimAtPath(tpv_path)
                    if not (tpv and tpv.IsValid() and tpv.IsA(UsdGeom.Camera)):
                        tpv = None if stage is None else stage.GetPrimAtPath("/World/ThirdPersonCamera")
                    tpv_ok = bool(tpv and tpv.IsValid() and tpv.IsA(UsdGeom.Camera))
                if fpv_ok and tpv_ok:
                    return
            except Exception:
                pass
            await app.next_update_async()

    async def _camera_capture_retry_loop(self) -> None:
        app = omni.kit.app.get_app()
        backoff = 0.25
        while True:
            wide_needed = False
            try:
                s = carb.settings.get_settings()
                wide_needed = bool(s.get("/gil/camera/wide_enabled") or False)
            except Exception:
                wide_needed = False
            fpv_ok = self._rgb_annot_fpv is not None
            tpv_ok = (self._rgb_annot_tpv is not None) or (not wide_needed)
            if fpv_ok and tpv_ok:
                return
            try:
                await self._wait_for_camera_prims(timeout_updates=120)
                self._setup_camera_capture()
                fpv_ok = self._rgb_annot_fpv is not None
                tpv_ok = (self._rgb_annot_tpv is not None) or (not wide_needed)
                if fpv_ok and tpv_ok:
                    return
            except Exception as e:
                carb.log_warn(f"[gil.h1_maze_walker] Camera capture retry failed: {e!r}")
            await app.next_update_async()
            await asyncio.sleep(backoff)
            backoff = min(2.0, backoff * 1.5)

    async def _gt_scene_loop(self) -> None:
        """
        Periodically sample `_gt_scene` without requiring an active websocket connection.

        This loop exits once the exporter finalizes (writes transforms.json + _DONE.txt).
        """
        while True:
            gt = getattr(self, "_gt_scene", None)
            if gt is None:
                await asyncio.sleep(0.2)
                continue
            try:
                await gt.maybe_capture(now=float(time.time()))
                if bool(getattr(gt, "is_complete", False)):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.05)

    def _ensure_scene_lighting(self, stage: Usd.Stage | None) -> None:
        """Ensure at least one light exists so RTX/Replicator frames aren't black."""
        if stage is None:
            return
        try:
            from pxr import UsdLux as _UsdLux, UsdGeom as _UsdGeom, Gf as _Gf
        except Exception:
            return
        try:
            # Create a stable container.
            root = stage.GetPrimAtPath("/World/Lights")
            if not (root and root.IsValid()):
                _UsdGeom.Xform.Define(stage, "/World/Lights")

            dome_path = "/World/Lights/DomeLight"
            dome_prim = stage.GetPrimAtPath(dome_path)
            if not (dome_prim and dome_prim.IsValid()):
                dome = _UsdLux.DomeLight.Define(stage, dome_path)
                dome.CreateIntensityAttr(2000.0)
                dome.CreateExposureAttr(4.0)
                dome.CreateColorAttr(_Gf.Vec3f(1.0, 1.0, 1.0))

            sun_path = "/World/Lights/SunLight"
            sun_prim = stage.GetPrimAtPath(sun_path)
            if not (sun_prim and sun_prim.IsValid()):
                sun = _UsdLux.DistantLight.Define(stage, sun_path)
                sun.CreateIntensityAttr(50000.0)
                sun.CreateExposureAttr(0.0)
                sun.CreateAngleAttr(0.53)  # roughly solar disc
                # Aim slightly downwards so corridors are lit.
                xform = _UsdGeom.Xformable(sun_prim)
                rot = xform.AddRotateXYZOp()
                rot.Set(_Gf.Vec3f(-35.0, 0.0, 25.0))
        except Exception as e:
            try:
                carb.log_warn(f"[gil.h1_maze_walker] Failed to ensure lights: {e!r}")
            except Exception:
                pass

    def _setup_camera_capture(self) -> None:
        """
        Create Replicator render products and RGB annotators for:
        - /World/Humanoid/FPVCamera (main)
        - /World/ThirdPersonCamera (wide)
        """
        if getattr(self, "_capture_configured", False) and self._rgb_annot_fpv is not None:
            try:
                self._maybe_attach_extra_cameras(force=True)
            except Exception:
                pass
            return
        try:
            s = carb.settings.get_settings()
            cap = s.get("/gil/camera/capture_enabled")
            cap = True if cap is None else bool(cap)
            if not cap:
                return
        except Exception:
            pass
        try:
            import omni.replicator.core as rep
        except Exception:
            carb.log_warn("[gil.h1_maze_walker] omni.replicator.core not available; images disabled")
            return
        self._rep = rep

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        try:
            self._ensure_scene_lighting(stage)
        except Exception:
            pass

        s = carb.settings.get_settings()
        fpv_path = s.get("/gil/camera/fpv_prim_path")
        fpv_path = "/World/Humanoid/FPVCamera" if fpv_path is None else str(fpv_path)
        tpv_path = s.get("/gil/camera/tpv_prim_path")
        tpv_path = "/World/ThirdPersonCamera" if tpv_path is None else str(tpv_path)
        try:
            from pxr import UsdGeom

            tpv_prim_chk = stage.GetPrimAtPath(tpv_path)
            if not (tpv_prim_chk and tpv_prim_chk.IsValid() and tpv_prim_chk.IsA(UsdGeom.Camera)):
                tpv_path = "/World/ThirdPersonCamera"
        except Exception:
            tpv_path = "/World/ThirdPersonCamera"

        fpv_prim = stage.GetPrimAtPath(fpv_path)
        if not (fpv_prim and fpv_prim.IsValid() and fpv_prim.IsA(UsdGeom.Camera)):
            root = stage.GetPrimAtPath("/World/Humanoid")
            if root and root.IsValid():
                for p in Usd.PrimRange(root):
                    try:
                        if not p.IsA(UsdGeom.Camera):
                            continue
                        nm = str(p.GetName() or "")
                        pp = p.GetPath().pathString
                        # Prefer Nvidia/IsaacLab-style camera_link cameras when present.
                        if "/World/Humanoid/camera_link/" in pp:
                            fpv_path = pp
                            fpv_prim = p
                            try:
                                s.set("/gil/camera/fpv_prim_path", fpv_path)
                            except Exception:
                                pass
                            break
                        # Fallback: common names.
                        if nm in ("front_cam", "PerspectiveCamera_robot", "camera", "fpv_cam"):
                            fpv_path = pp
                            fpv_prim = p
                            try:
                                s.set("/gil/camera/fpv_prim_path", fpv_path)
                            except Exception:
                                pass
                            break
                    except Exception:
                        continue
        tpv_prim = stage.GetPrimAtPath(tpv_path)
        if not (fpv_prim and fpv_prim.IsValid()):
            raise RuntimeError(f"Camera prims not ready (fpv={fpv_path})")

        # Keep render products tiny. Full-res CaptureAll freezes this Vulkan path.
        w, h = self._capture_wh()
        ww, wh = w, h
        try:
            ww0 = s.get("/gil/camera/wide_width")
            wh0 = s.get("/gil/camera/wide_height")
            if ww0 is not None:
                ww = int(ww0)
            if wh0 is not None:
                wh = int(wh0)
        except Exception:
            pass

        wide_enabled = s.get("/gil/camera/wide_enabled")
        wide_enabled = False if wide_enabled is None else bool(wide_enabled)

        # Create render products
        rp_fpv = rep.create.render_product(fpv_path, (w, h))
        self._rp_fpv_path = rp_fpv.path

        rgb_fpv = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb_fpv.attach([rp_fpv])
        self._rgb_annot_fpv = rgb_fpv

        # Optional: export a WorldSculpt-compatible "gt_scene" dataset from Replicator annotations.
        self._gt_scene = None
        try:
            s = carb.settings.get_settings()
            gt_enabled = s.get("/gil/gt_scene/enabled")
            gt_enabled = False if gt_enabled is None else bool(gt_enabled)
        except Exception:
            gt_enabled = False
        if gt_enabled:
            try:
                self._gt_scene = _GtSceneExporter(self, rp_fpv=rp_fpv, camera_prim_path=str(fpv_path), wh=(w, h))
            except Exception as e:
                carb.log_warn(f"[gil.h1_maze_walker] GT export init failed (disabled): {e!r}")
                self._gt_scene = None

        self._rp_tpv_path = ""
        self._rgb_annot_tpv = None
        if wide_enabled:
            rp_tpv = rep.create.render_product(tpv_path, (ww, wh))
            self._rp_tpv_path = rp_tpv.path
            rgb_tpv = rep.AnnotatorRegistry.get_annotator("rgb")
            rgb_tpv.attach([rp_tpv])
            self._rgb_annot_tpv = rgb_tpv

        # Optional: capture all robot cameras (front/world/wrist) as additional streams.
        capture_all = s.get("/gil/camera/capture_all")
        # Default to false; extra camera render products add latency and can destabilize sim startup.
        capture_all = False if capture_all is None else bool(capture_all)
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
                cam_paths = sorted(set(cam_paths))

                def _allow_extra(cp: str) -> bool:
                    return self._allow_extra_camera(cp)

                cam_paths = [cp for cp in cam_paths if _allow_extra(cp)][:8]
                for cp in cam_paths:
                    if cp == fpv_path:
                        continue
                    if cp == tpv_path:
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

        try:
            carb.log_warn(
                f"[gil.h1_maze_walker] Camera capture FPV {w}x{h} wide={wide_enabled} "
                f"fpv_path={fpv_path} tpv_path={tpv_path} extra={sorted(self._rgb_by_cam.keys())}"
            )
        except Exception:
            pass
        self._capture_configured = True

    def _maybe_attach_extra_cameras(self, *, force: bool = False) -> None:
        """
        If /gil/camera/capture_all is enabled, attach any newly-created cameras under /World/Humanoid.
        This is needed for H1-2 bringup because the sensor suite can be created after our initial capture setup.
        """
        try:
            s = carb.settings.get_settings()
            capture_all = s.get("/gil/camera/capture_all")
            capture_all = False if capture_all is None else bool(capture_all)
            if not capture_all:
                return
            now = float(time.time())
            if (not force) and (now - float(getattr(self, "_last_extra_cam_scan_t", 0.0))) < 2.0:
                return
            self._last_extra_cam_scan_t = now
        except Exception:
            return

        rep = getattr(self, "_rep", None)
        if rep is None:
            return

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        try:
            from pxr import UsdGeom
        except Exception:
            return

        try:
            s = carb.settings.get_settings()
            fpv_path = str(s.get("/gil/camera/fpv_prim_path") or "/World/Humanoid/FPVCamera")
            tpv_path = str(s.get("/gil/camera/tpv_prim_path") or "/World/ThirdPersonCamera")
            w, h = self._capture_wh()
        except Exception:
            fpv_path = "/World/Humanoid/FPVCamera"
            tpv_path = "/World/ThirdPersonCamera"
            w, h = 160, 90

        root = stage.GetPrimAtPath("/World/Humanoid")
        if not (root and root.IsValid()):
            return

        cam_paths: list[str] = []
        for p in Usd.PrimRange(root):
            if p.IsA(UsdGeom.Camera):
                cam_paths.append(p.GetPath().pathString)

        cam_paths = sorted(set(cam_paths))

        def _allow_extra(cp: str) -> bool:
            return self._allow_extra_camera(cp)

        # Purge legacy/unmounted cams already attached (prevents them from lingering in the list).
        try:
            for cp0 in list(getattr(self, "_rgb_by_cam", {}).keys()):
                if not _allow_extra(cp0):
                    try:
                        self._rgb_by_cam.pop(cp0, None)
                    except Exception:
                        pass
                    try:
                        self._rp_by_cam.pop(cp0, None)
                    except Exception:
                        pass
        except Exception:
            pass

        cam_paths = [cp for cp in cam_paths if _allow_extra(cp)][:8]

        for cp in cam_paths:
            if cp in (fpv_path, tpv_path):
                continue
            if cp in self._rgb_by_cam:
                continue
            try:
                rp = rep.create.render_product(cp, (w, h))
                rgb = rep.AnnotatorRegistry.get_annotator("rgb")
                rgb.attach([rp])
                self._rp_by_cam[cp] = rp.path
                self._rgb_by_cam[cp] = rgb
            except Exception:
                continue

    def _capture_wh(self) -> tuple[int, int]:
        try:
            s = carb.settings.get_settings()
            w = s.get("/gil/camera/fpv_width")
            h = s.get("/gil/camera/fpv_height")
            w = 160 if w is None else max(80, int(w))
            h = 90 if h is None else max(45, int(h))
            return w, h
        except Exception:
            return 160, 90

    def _is_body_mounted_camera(self, cam_path: str) -> bool:
        """True for cameras parented under H1 links (chest/wrists), not synthetic FPV/TPV/gil_cameras."""
        pl = str(cam_path).replace("\\", "/")
        if not pl.startswith("/World/Humanoid/"):
            return False
        if "/gil_cameras/" in pl:
            return False
        if pl.rstrip("/").endswith("/FPVCamera"):
            return False
        skip_names = ("ThirdPersonCamera", "OmniverseKit_Persp")
        if any(n in pl for n in skip_names):
            return False
        return True

    def _allow_extra_camera(self, cam_path: str) -> bool:
        pl = str(cam_path).replace("\\", "/")
        if pl.endswith("/ChaseCamera"):
            return True
        if pl.startswith("/World/Humanoid/gil_cameras/"):
            return True
        # Allow IsaacLab/Unitree sensor suites (Nvidia-style) under camera_link.
        # This is how the "real" robot camera hierarchy is typically represented.
        if "/World/Humanoid/camera_link/" in pl:
            return True
        # Allow other body-mounted cameras by default (exclude synthetic FPV/TPV helpers).
        try:
            if self._is_body_mounted_camera(pl):
                return True
        except Exception:
            pass
        try:
            v = str(carb.settings.get_settings().get("/gil/humanoid/variant") or "").lower().strip()
        except Exception:
            v = ""
        if v in ("g1", "unitree_g1", "h1_2", "h1-2"):
            if not pl.startswith("/World/Humanoid/"):
                return False
            if pl.rstrip("/").endswith("/FPVCamera"):
                return False
            if "ThirdPersonCamera" in pl or "OmniverseKit_Persp" in pl:
                return False
            return True
        return False

    def _collage_enabled(self) -> bool:
        try:
            v = carb.settings.get_settings().get("/gil/camera/collage_enabled")
            return False if v is None else bool(v)
        except Exception:
            return True

    def _rgba_to_pil(self, rgba):
        try:
            import numpy as _np
            from PIL import Image

            if isinstance(rgba, dict):
                rgba = rgba.get("data") or rgba.get("rgb") or rgba.get("buffer") or rgba
            arr = _np.asarray(rgba)
            if arr.dtype.kind == "f":
                arr = _np.clip(arr * 255.0, 0.0, 255.0).astype("uint8")
            else:
                arr = arr.astype("uint8", copy=False)
            if arr.ndim == 3 and arr.shape[2] >= 3:
                arr = arr[:, :, :3]
            if arr.size == 0:
                return None
            return Image.fromarray(arr)
        except Exception:
            return None

    def _compose_camera_collage(self, tiles: list[tuple[str, Any]], *, rotate_deg: float = 0.0) -> str:
        """One labeled grid JPEG for the agent (all cams, low res)."""
        try:
            from PIL import Image, ImageDraw

            tw, th = self._capture_wh()
            labeled: list[tuple[str, Any]] = []
            for name, raw in tiles:
                im = self._rgba_to_pil(raw)
                if im is None:
                    continue
                r = float(rotate_deg)
                if abs(r) > 1e-6 and name.lower() in ("fpv", "front"):
                    k = int(round(r / 90.0)) % 4
                    if k:
                        import numpy as _np

                        arr = _np.rot90(_np.asarray(im), k=k)
                        im = Image.fromarray(arr)
                im = im.resize((tw, th))
                labeled.append((name, im))
            if not labeled:
                return ""
            cols = 3 if len(labeled) > 2 else max(1, len(labeled))
            rows = (len(labeled) + cols - 1) // cols
            pad = 18
            canvas = Image.new("RGB", (cols * tw, rows * (th + pad)), (18, 18, 18))
            draw = ImageDraw.Draw(canvas)
            for i, (name, im) in enumerate(labeled):
                r, c = divmod(i, cols)
                x, y = c * tw, r * (th + pad)
                canvas.paste(im, (x, y + pad))
                draw.text((x + 4, y + 2), str(name)[:28], fill=(240, 240, 80))
            buf = BytesIO()
            canvas.save(buf, format="JPEG", quality=70)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return "data:image/jpeg;base64," + b64
        except Exception:
            return ""

    def _encode_jpeg_data_url(self, rgba, *, rotate_deg: float = 0.0) -> str:
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

            # Some Isaac/Replicator camera pipelines can deliver a frame that is effectively rolled
            # relative to the expected "horizon upright" view. Allow a deterministic correction.
            r = float(rotate_deg)
            if abs(r) > 1e-6:
                # Normalize to multiples of 90 degrees.
                k = int(round(r / 90.0)) % 4
                if k != 0:
                    arr = _np.rot90(arr, k=k)
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

    def _standing_base_z(self, requested: float | None = None) -> float:
        """Pelvis/root height for spawn and episode reset.

        Copying the live pose while fallen (~0.08 m) plants the H1 inside the ground
        and it immediately ragdolls. Official H1 pelvis is ~1.0–1.05 m standing.
        """
        s = carb.settings.get_settings()
        default_z = s.get("/gil/humanoid/spawn_z")
        try:
            variant = str(s.get("/gil/humanoid/variant") or "h1").lower().strip()
        except Exception:
            variant = "h1"
        is_g1 = variant in ("g1", "unitree_g1")
        if default_z is None:
            z0 = 0.76 if is_g1 else 1.05
        else:
            z0 = float(default_z)
        z = z0 if requested is None else float(requested)
        lo, hi, floor = (0.72, 0.85, 0.55) if is_g1 else (1.00, 1.20, 0.90)
        if not math.isfinite(z) or z < floor:
            z = z0
        return float(min(hi, max(lo, z)))

    def _maze_spawn_xy(self) -> tuple[float, float]:
        s = carb.settings.get_settings()
        try:
            origin_x = float(s.get("/gil/maze/origin_x") or -4.0)
            origin_y = float(s.get("/gil/maze/origin_y") or -4.0)
            cell = float(s.get("/gil/maze/cell_size") or 1.4)
            scx = int(s.get("/gil/maze/spawn_cell_x") or 1)
            scy = int(s.get("/gil/maze/spawn_cell_y") or 1)
            w = int(s.get("/gil/maze/width") or 9)
            h = int(s.get("/gil/maze/height") or 9)
            scx = max(0, min(w - 1, int(scx)))
            scy = max(0, min(h - 1, int(scy)))
            return origin_x + (scx + 0.5) * cell, origin_y + (scy + 0.5) * cell
        except Exception:
            return -1.9, -1.9

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
        force_h1_policy = _carb_bool(force_h1_policy_setting, False)
        # For H1-2: if the policy init fails, we still want visible walking instead of a mannequin sliding.
        # So default to enabling the dynamic-control + simple-gait fallback unless the user explicitly disables it.
        fallback_base_velocity = _carb_bool(fallback_base_vel_setting, True)
        fallback_simple_gait = _carb_bool(fallback_simple_gait_setting, True)
        require_policy = _carb_bool(require_policy_setting, False)

        # If the user explicitly requests "real locomotion only", disable all cheats.
        if require_policy:
            fallback_base_velocity = False
            fallback_simple_gait = False

        # ROS2 config (inside Isaac). Can be overridden via carb settings.
        ros2_enable_setting = s.get("/gil/ros2/enable")
        ros2_enable = _carb_bool(ros2_enable_setting, True)

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
            turn_in_place_err=gf("/gil/walker/turn_in_place_err", 0.8),
            turn_min_vx_scale=gf("/gil/walker/turn_min_vx_scale", 0.12),
            stop_radius=gf("/gil/walker/stop_radius", 0.6),
            mode=gs("/gil/walker/mode", "goal").lower(),
            cmd_timeout_s=gf("/gil/walker/cmd_timeout_s", 0.6),
            min_upright_base_z_m=gf("/gil/walker/min_upright_base_z_m", 0.55),
            h1_wz_scale=gf("/gil/walker/h1_wz_scale", 0.35),
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
                # Do NOT clear yet; we'll retry until it takes (or we give up).
                reset_req = dict(self._pending_reset) if isinstance(self._pending_reset, dict) else self._pending_reset
        if isinstance(reset_req, dict):
            try:
                rx0 = float(reset_req.get("x", 0.0))
                ry0 = float(reset_req.get("y", 0.0))
                yaw0 = float(reset_req.get("yaw", 0.0))
                attempt = int(reset_req.get("_attempt", 0) or 0)
                z_req = reset_req.get("z")
                try:
                    z_req_f = None if z_req is None else float(z_req)
                except Exception:
                    z_req_f = None
                # Always spawn at standing pelvis height. Reusing a fallen z plants the robot in the ground.
                z_keep = self._standing_base_z(requested=z_req_f)
                # Prefer: if we have a policy robot handle, use its API.
                if self._h1 is not None and getattr(self._h1, "robot", None) is not None:
                    try:
                        pos = np.array([rx0, ry0, float(z_keep)], dtype=np.float32)
                        # Quaternion (w, x, y, z) for yaw about Z
                        cy = math.cos(yaw0 * 0.5)
                        sy = math.sin(yaw0 * 0.5)
                        quat = np.array([cy, 0.0, 0.0, sy], dtype=np.float32)
                        if hasattr(self._h1.robot, "set_world_pose"):
                            self._h1.robot.set_world_pose(pos, quat)
                        # Reset articulation state so we don't keep a ragdolled pose.
                        try:
                            if hasattr(self._h1, "post_reset"):
                                self._h1.post_reset()
                        except Exception:
                            pass
                        try:
                            if hasattr(self._h1.robot, "set_joint_positions") and hasattr(self._h1, "default_pos"):
                                self._h1.robot.set_joint_positions(self._h1.default_pos)
                            if hasattr(self._h1.robot, "set_joint_velocities"):
                                dv = getattr(self._h1, "default_vel", None)
                                if dv is None:
                                    dv = np.zeros_like(getattr(self._h1, "default_pos", np.zeros(1)))
                                self._h1.robot.set_joint_velocities(dv)
                            if hasattr(self._h1.robot, "set_linear_velocity"):
                                self._h1.robot.set_linear_velocity(np.array([0.0, 0.0, 0.0], dtype=np.float32))
                            if hasattr(self._h1.robot, "set_angular_velocity"):
                                self._h1.robot.set_angular_velocity(np.array([0.0, 0.0, 0.0], dtype=np.float32))
                        except Exception:
                            pass
                    except Exception:
                        pass

                # Dynamic-control pose reset if available.
                if self._dc is not None and (self._dc_art is not None or self._dc_base_body is not None):
                    try:
                        from omni.isaac.dynamic_control import _dynamic_control  # type: ignore

                        tf = _dynamic_control.Transform()
                        z_keep = self._standing_base_z(requested=z_req_f)
                        tf.p = _dynamic_control.Vec3(rx0, ry0, float(z_keep))
                        cy = math.cos(yaw0 * 0.5)
                        sy = math.sin(yaw0 * 0.5)
                        tf.r = _dynamic_control.Quat(cy, 0.0, 0.0, sy)
                        # Prefer teleporting the articulation root (moves the whole robot).
                        try:
                            if self._dc_art is not None and hasattr(self._dc, "set_articulation_root_pose"):
                                self._dc.set_articulation_root_pose(self._dc_art, tf)
                            if self._dc_art is not None and hasattr(self._dc, "set_articulation_root_linear_velocity"):
                                self._dc.set_articulation_root_linear_velocity(self._dc_art, (0.0, 0.0, 0.0))
                            if self._dc_art is not None and hasattr(self._dc, "set_articulation_root_angular_velocity"):
                                self._dc.set_articulation_root_angular_velocity(self._dc_art, (0.0, 0.0, 0.0))
                        except Exception:
                            pass

                        # Fallback: move the resolved base rigid body.
                        if self._dc_base_body is not None:
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
                        xlate.Set((rx0, ry0, float(z_keep)))
                        rot.Set((0.0, 0.0, float(yaw0 * 180.0 / math.pi)))
                except Exception:
                    pass

                # Confirm the reset took (and if not, keep `_pending_reset` for retries).
                ok = False
                try:
                    if self._dc is not None and self._dc_base_body is not None:
                        tf2 = self._dc.get_rigid_body_pose(self._dc_base_body)
                        p2 = getattr(tf2, "p", None)
                        if p2 is not None:
                            px = getattr(p2, "x", None)
                            if px is None:
                                x2, y2, z2 = float(p2[0]), float(p2[1]), float(p2[2])
                            else:
                                x2, y2, z2 = float(p2.x), float(p2.y), float(p2.z)
                            ok = (abs(x2 - rx0) <= 0.85) and (abs(y2 - ry0) <= 0.85) and (z2 >= 0.55)
                except Exception:
                    ok = False
                if not ok:
                    # If dynamic-control isn't available, fall back to checking the USD root prim transform.
                    try:
                        stage = omni.usd.get_context().get_stage()
                        prim = None if stage is None else stage.GetPrimAtPath(str(cfg.prim_path))
                        if prim and prim.IsValid():
                            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                            p = m.ExtractTranslation()
                            x2, y2, z2 = float(p[0]), float(p[1]), float(p[2])
                            ok = (abs(x2 - rx0) <= 1.25) and (abs(y2 - ry0) <= 1.25) and (z2 >= 0.35)
                    except Exception:
                        ok = False

                with self._lock:
                    if ok:
                        self._pending_reset = None
                    else:
                        attempt2 = attempt + 1
                        if attempt2 >= 40:
                            self._pending_reset = None
                            try:
                                carb.log_warn(
                                    f"[gil.h1_maze_walker] reset_episode did not converge after {attempt2} attempts "
                                    f"target=({rx0:.2f},{ry0:.2f})"
                                )
                            except Exception:
                                pass
                        else:
                            self._pending_reset = {"x": rx0, "y": ry0, "yaw": yaw0, "_attempt": attempt2}
                            try:
                                if attempt2 in (1, 5, 10, 20, 30):
                                    carb.log_warn(
                                        f"[gil.h1_maze_walker] reset_episode retry attempt={attempt2} target=({rx0:.2f},{ry0:.2f})"
                                    )
                            except Exception:
                                pass
            except Exception:
                with self._lock:
                    self._pending_reset = None

        # Base pose (works with or without policy controller)
        #
        # IMPORTANT: Prefer dynamic-control rigid-body pose (pelvis/root) even when using the H1 policy.
        # Many humanoid USDs keep the articulation root Xform fixed near the origin; using that pose makes
        # the robot appear "stuck" at (0,0) and breaks reset + external steering.
        # Seed with last known good pose (prevents "teleport" if dynamic-control reads fail transiently).
        rx = ry = rz = yaw = 0.0
        try:
            lg = getattr(self, "_last_good_base_pose", None)
            if isinstance(lg, dict) and all(k in lg for k in ("x", "y", "z", "yaw")):
                rx = float(lg["x"])
                ry = float(lg["y"])
                rz = float(lg["z"])
                yaw = float(lg["yaw"])
        except Exception:
            pass
        # Prefer dynamic-control pose when available; it reflects physical bodies.
        if self._dc is not None and self._dc_base_body is not None:
            try:
                tf = self._dc.get_rigid_body_pose(self._dc_base_body)
                p = getattr(tf, "p", None)
                q = getattr(tf, "r", None)
                if p is not None:
                    px = getattr(p, "x", None)
                    if px is None:
                        rx, ry, rz = float(p[0]), float(p[1]), float(p[2])
                    else:
                        rx, ry, rz = float(p.x), float(p.y), float(p.z)
                if q is not None:
                    qw = getattr(q, "w", None)
                    if qw is None:
                        qw, qx, qy, qz = float(q[0]), float(q[1]), float(q[2]), float(q[3])
                    else:
                        qx = float(getattr(q, "x", 0.0))
                        qy = float(getattr(q, "y", 0.0))
                        qz = float(getattr(q, "z", 0.0))
                        qw = float(qw)
                    yaw = float(math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz)))
                # Update cache on successful dynamic-control read.
                try:
                    self._last_good_base_pose = {"x": float(rx), "y": float(ry), "z": float(rz), "yaw": float(yaw)}
                except Exception:
                    pass
            except Exception:
                pass

        # Fall back to policy/root pose if needed.
        if (
            (getattr(self, "_last_good_base_pose", None) is None)
            and (rx == 0.0 and ry == 0.0 and rz == 0.0)
            and self._h1 is not None
            and self._using_h1_policy
        ):
            try:
                pos, quat = self._h1.robot.get_world_pose()
                rx, ry, rz = float(pos[0]), float(pos[1]), float(pos[2])
                yaw = float(quat_to_euler_angles(quat)[2])
            except Exception:
                pass

        # Last resort: USD pelvis transform.
        if (getattr(self, "_last_good_base_pose", None) is None) and (rx == 0.0 and ry == 0.0 and rz == 0.0):
            try:
                stage = omni.usd.get_context().get_stage()
                prim = None
                if stage is not None:
                    pelvis_path = f"{cfg.prim_path}/pelvis"
                    pelvis_prim = stage.GetPrimAtPath(pelvis_path)
                    if pelvis_prim and pelvis_prim.IsValid():
                        prim = pelvis_prim
                    else:
                        prim = stage.GetPrimAtPath(cfg.prim_path)
                if prim and prim.IsValid():
                    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                    p = m.ExtractTranslation()
                    rx, ry, rz = float(p[0]), float(p[1]), float(p[2])
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
            # Many locomotion policies (including H1) do not rotate well at vx=0.
            # In goal mode we already enforce a small forward component while turning; mirror that here.
            try:
                if (
                    self._h1 is not None
                    and self._using_h1_policy
                    and abs(float(cmd[2])) > 1e-4
                    and abs(float(cmd[0])) < float(cfg.vx) * float(cfg.turn_min_vx_scale)
                ):
                    cmd = cmd.copy()
                    cmd[0] = float(cfg.vx) * float(cfg.turn_min_vx_scale)
            except Exception:
                pass
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
                if abs(float(yaw_err)) > float(cfg.turn_in_place_err):
                    vx = float(cfg.vx) * float(max(0.0, min(1.0, float(cfg.turn_min_vx_scale))))
                else:
                    vx = float(cfg.vx)
                cmd = np.array([vx, 0.0, wz], dtype=np.float32)

        # Safety latch: if the humanoid is low (fallen/unstable), do not apply locomotion commands.
        # This prevents the "crawling while lying on the ground" behavior.
        try:
            if 0.0 < float(rz) < float(cfg.min_upright_base_z_m):
                cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
                with self._lock:
                    try:
                        self._latest_cmd = cmd.copy()
                        self._latest_cmd_t = time.time()
                    except Exception:
                        pass
        except Exception:
            pass

        did_apply_locomotion = False

        # Apply command via policy controller when available.
        if self._h1 is not None and self._using_h1_policy:
            try:
                # Policy yaw-rate scaling.
                #
                # We use rad/sec in GIL. Some Isaac builds/policies interpret cmd[2] differently; in practice,
                # applying a large deg/s conversion here can easily overdrive turns and cause tipping.
                # Therefore we keep the command in rad/sec and apply only a conservative stability scale.
                cmd_policy = cmd.copy()
                cmd_policy[2] = float(cmd_policy[2]) * float(cfg.h1_wz_scale)
                self._h1.forward(step_size, cmd_policy)
                did_apply_locomotion = True
            except Exception:
                pass

        # Fallback: drive base rigid body velocities (navigation-only, not real walking).
        #
        # IMPORTANT: if the user requested `require_policy=true`, never engage this fallback.
        # It's exactly the "floating/sliding" behavior users complain about when they expect real footsteps.
        if (not self._using_h1_policy) and (not bool(cfg.require_policy)) and bool(cfg.fallback_base_velocity) and self._dc is not None and self._dc_base_body is not None:
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
                # Dynamic control expects linear velocity in *stage units/sec*.
                # Our `cmd` is in meters/sec. Convert using stage meters-per-unit when available.
                meters_per_unit = 1.0
                try:
                    stage2 = omni.usd.get_context().get_stage()
                    if stage2 is not None:
                        try:
                            meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage2))
                        except Exception:
                            meters_per_unit = float(stage2.GetMetadata("metersPerUnit") or 1.0)
                except Exception:
                    meters_per_unit = 1.0
                if meters_per_unit <= 0.0:
                    meters_per_unit = 1.0
                vx_units = float(vxw / meters_per_unit)
                vy_units = float(vyw / meters_per_unit)

                # Prefer driving the articulation root velocities when available.
                # Setting a single link's velocity inside an articulation can be heavily damped/overridden by the solver.
                applied_via = "rb"
                # If articulation-root velocity APIs are unavailable (common in some Isaac builds),
                # fall back to a kinematic root-pose integrator. This makes cmd_vel actually move the robot
                # (useful for navigation debugging) while the simple gait provides "walking" visuals.
                kinematic_enabled = False
                try:
                    kin_setting = carb.settings.get_settings().get("/gil/walker/fallback_base_velocity_kinematic")
                    # Default OFF: kinematic root integration ignores gravity (looks like 0G).
                    kinematic_enabled = _carb_bool(kin_setting, False)
                except Exception:
                    kinematic_enabled = True

                try:
                    if (not kinematic_enabled) and self._dc_art is not None and hasattr(self._dc, "set_articulation_root_linear_velocity"):
                        self._dc.set_articulation_root_linear_velocity(self._dc_art, (vx_units, vy_units, 0.0))
                        applied_via = "art_root"
                    else:
                        self._dc.set_rigid_body_linear_velocity(self._dc_base_body, (vx_units, vy_units, 0.0))
                except Exception:
                    # Fall back to rigid body velocity.
                    try:
                        self._dc.set_rigid_body_linear_velocity(self._dc_base_body, (vx_units, vy_units, 0.0))
                        applied_via = "rb"
                    except Exception:
                        pass

                try:
                    if (not kinematic_enabled) and self._dc_art is not None and hasattr(self._dc, "set_articulation_root_angular_velocity"):
                        self._dc.set_articulation_root_angular_velocity(self._dc_art, (0.0, 0.0, float(cmd[2])))
                        applied_via = "art_root"
                    else:
                        self._dc.set_rigid_body_angular_velocity(self._dc_base_body, (0.0, 0.0, float(cmd[2])))
                except Exception:
                    try:
                        self._dc.set_rigid_body_angular_velocity(self._dc_base_body, (0.0, 0.0, float(cmd[2])))
                        applied_via = "rb"
                    except Exception:
                        pass

                # Kinematic integrator (pose-based) when enabled and supported.
                try:
                    if kinematic_enabled and self._dc is not None and self._dc_base_body is not None and (
                        hasattr(self._dc, "set_rigid_body_pose") or (self._dc_art is not None and hasattr(self._dc, "set_articulation_root_pose"))
                    ):
                        from omni.isaac.dynamic_control import _dynamic_control  # type: ignore

                        # Read current rigid-body pose as a base for integration.
                        cur_tf = self._dc.get_rigid_body_pose(self._dc_base_body)
                        p = getattr(cur_tf, "p", None)
                        if p is not None:
                            px = getattr(p, "x", None)
                            if px is None:
                                x0, y0, z0 = float(p[0]), float(p[1]), float(p[2])
                            else:
                                x0, y0, z0 = float(p.x), float(p.y), float(p.z)
                        else:
                            x0, y0, z0 = float(rx), float(ry), float(rz)

                        # Integrate in stage units.
                        x1 = float(x0 + vx_units * float(step_size))
                        y1 = float(y0 + vy_units * float(step_size))
                        yaw1 = float(yaw + float(cmd[2]) * float(step_size))
                        cy2 = math.cos(0.5 * yaw1)
                        sy2 = math.sin(0.5 * yaw1)

                        tfk = _dynamic_control.Transform()
                        tfk.p = _dynamic_control.Vec3(float(x1), float(y1), float(z0))
                        tfk.r = _dynamic_control.Quat(float(cy2), 0.0, 0.0, float(sy2))
                        # Prefer rigid-body pose drive (works reliably for "teleport"/debug motion).
                        did_pose = False
                        try:
                            if hasattr(self._dc, "set_rigid_body_pose"):
                                self._dc.set_rigid_body_pose(self._dc_base_body, tfk)
                                applied_via = "kin_rb_pose"
                                did_pose = True
                        except Exception:
                            did_pose = False
                        if (not did_pose) and self._dc_art is not None and hasattr(self._dc, "set_articulation_root_pose"):
                            try:
                                self._dc.set_articulation_root_pose(self._dc_art, tfk)
                                applied_via = "kin_art_pose"
                                did_pose = True
                            except Exception:
                                did_pose = False

                        # Keep velocities at zero so PhysX doesn't "fight" the pose-based integrator.
                        try:
                            if hasattr(self._dc, "set_rigid_body_linear_velocity"):
                                self._dc.set_rigid_body_linear_velocity(self._dc_base_body, (0.0, 0.0, 0.0))
                            if hasattr(self._dc, "set_rigid_body_angular_velocity"):
                                self._dc.set_rigid_body_angular_velocity(self._dc_base_body, (0.0, 0.0, 0.0))
                        except Exception:
                            pass
                except Exception:
                    pass
                did_apply_locomotion = True
                # Breadcrumb: confirm we are attempting to apply velocities (rate-limited; warn-level).
                try:
                    now2 = time.time()
                    if (abs(float(cmd[0])) + abs(float(cmd[1])) + abs(float(cmd[2]))) > 1e-4 and (
                        (now2 - float(getattr(self, "_dbg_last_warn_t", 0.0))) >= 1.0
                    ):
                        self._dbg_last_warn_t = now2
                        carb.log_warn(
                            f"[gil.h1_maze_walker] dc_apply via={applied_via} base={getattr(self, '_dc_base_path', '')!s} art={getattr(self, '_dc_art_path', '')!s} "
                            f"cmd_robot=({float(cmd[0]):.3f},{float(cmd[1]):.3f},{float(cmd[2]):.3f}) "
                            f"cmd_world=({vxw:.3f},{vyw:.3f},{float(cmd[2]):.3f}) "
                            f"metersPerUnit={meters_per_unit:.3f} v_units=({vx_units:.3f},{vy_units:.3f})"
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

        # Images: physics thread only peeks FPV. Extra cams + collage run on the async send loop.
        if (now - self._last_image_send_t) >= 1.0 and not self._collage_enabled():
            self._last_image_send_t = now
            img_main = ""
            img_wide = ""
            try:
                if self._rgb_annot_fpv is not None:
                    img_main = self._encode_jpeg_data_url(self._rgb_annot_fpv.get_data())
                if self._rgb_annot_tpv is not None:
                    img_wide = self._encode_jpeg_data_url(self._rgb_annot_tpv.get_data())
            except Exception:
                img_main = ""
                img_wide = ""
            if img_main or img_wide:
                payload = {
                    "type": "scene_image",
                    "robot_kind": "humanoid",
                    "image": img_main,
                    "image_wide": img_wide,
                    "images": {},
                    "base": {"x": rx, "y": ry, "z": float(rz), "yaw": yaw},
                    "objects": {},
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
            enable = True if enable is None else bool(enable)
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
                    # Use WARN so it is visible in typical Isaac log configurations.
                    carb.log_warn(f"[gil.h1_maze_walker] Connected to controls websocket: {uri}")
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
            if t in ("cmd_vel", "preview_vel"):
                vx = float(data.get("vx", 0.0))
                vy = float(data.get("vy", 0.0))
                wz = float(data.get("wz", 0.0))
                with self._lock:
                    self._latest_cmd = np.array([vx, vy, wz], dtype=np.float32)
                    self._latest_cmd_t = time.time()
                    self._dream_preview = t == "preview_vel"
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
                    carb.log_warn(f"[gil.h1_maze_walker] set_goal rx x={gx:.2f} y={gy:.2f}")
                    s = carb.settings.get_settings()
                    s.set("/gil/task/goal_x", gx)
                    s.set("/gil/task/goal_y", gy)
                except Exception:
                    pass
                continue
            if t == "reset_episode":
                # Best-effort: reset is applied inside physics step so it stays in sync with the sim.
                try:
                    req = {
                        "x": float(data.get("x", 0.0)),
                        "y": float(data.get("y", 0.0)),
                        "yaw": float(data.get("yaw", 0.0)),
                        "_attempt": 0,
                    }
                    if data.get("z") is not None:
                        req["z"] = float(data.get("z"))
                except Exception:
                    req = {"x": 0.0, "y": 0.0, "yaw": 0.0, "z": 1.05, "_attempt": 0}
                with self._lock:
                    # Reset should not be "fought" by goal mode or a stale cmd_vel.
                    self._mode_override = "external"
                    try:
                        self._latest_cmd = np.array([0.0, 0.0, 0.0], dtype=np.float32)
                        self._latest_cmd_t = 0.0
                    except Exception:
                        pass
                    # Clear cached pose so we don't keep reporting a stale location while DC handles settle.
                    try:
                        self._last_good_base_pose = None
                    except Exception:
                        pass
                    self._pending_reset = req
                    self._dream_preview = False
                # Immediate USD authoring so the user sees a response even before physics callback is active.
                try:
                    cfg = self._read_cfg()
                    stage = omni.usd.get_context().get_stage()
                    prim = None if stage is None else stage.GetPrimAtPath(str(cfg.prim_path))
                    if prim and prim.IsValid():
                        # Keep current Z if possible (avoids "dropping" the robot).
                        z_keep = 0.80
                        try:
                            m0 = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                            p0 = m0.ExtractTranslation()
                            z_keep = float(p0[2])
                        except Exception:
                            z_keep = 0.80
                        if not math.isfinite(float(z_keep)):
                            z_keep = 0.80
                        z_keep = float(min(1.20, max(0.65, float(z_keep))))
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
                        xlate.Set((float(req["x"]), float(req["y"]), float(z_keep)))
                        rot.Set((0.0, 0.0, float(float(req["yaw"]) * 180.0 / math.pi)))
                        # Seed cached pose for state streaming.
                        with self._lock:
                            self._last_good_base_pose = {"x": float(req["x"]), "y": float(req["y"]), "z": float(z_keep), "yaw": float(req["yaw"])}
                except Exception:
                    pass
                # If we don't start the timeline, `_on_physics_step` never runs and the reset is never applied.
                # This shows up as "reset doesn't work" until the next cmd_vel arrives.
                try:
                    if self._timeline is not None and (not self._timeline.is_playing()):
                        carb.log_info("[gil.h1_maze_walker] reset_episode received while paused: starting timeline")
                        self._timeline.play()
                except Exception:
                    pass
                continue
            if t == "set_embodiment":
                try:
                    s = carb.settings.get_settings()
                    usd_rel = str(data.get("usd_rel") or "")
                    variant = str(data.get("variant") or data.get("key") or "")
                    if usd_rel:
                        s.set("/gil/humanoid/usd_rel", usd_rel)
                    if variant:
                        s.set("/gil/humanoid/variant", variant)
                    with self._lock:
                        self._embodiment = {"usd_rel": usd_rel, "variant": variant, "key": str(data.get("key") or "")}
                    carb.log_info(f"[gil.h1_maze_walker] set_embodiment usd_rel={usd_rel} variant={variant}")
                except Exception as e:
                    carb.log_warn(f"[gil.h1_maze_walker] set_embodiment failed: {e!r}")
                continue
            if t == "load_world":
                try:
                    s = carb.settings.get_settings()
                    generator = str(data.get("generator") or "maze_dfs")
                    seed = int(data.get("seed", 0) or 0)
                    s.set("/gil/world/generator", generator)
                    s.set("/gil/maze/seed", seed)
                    if data.get("width") is not None:
                        s.set("/gil/maze/width", int(data.get("width")))
                    if data.get("height") is not None:
                        s.set("/gil/maze/height", int(data.get("height")))
                    if data.get("cell_size") is not None:
                        s.set("/gil/maze/cell_size", float(data.get("cell_size")))
                    if data.get("rebuild"):
                        s.set("/gil/maze/rebuild", True)
                    with self._lock:
                        self._world_spec = {"generator": generator, "seed": seed}
                    carb.log_info(f"[gil.h1_maze_walker] load_world generator={generator} seed={seed}")
                except Exception as e:
                    carb.log_warn(f"[gil.h1_maze_walker] load_world failed: {e!r}")
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
                # Hot-reload defense: older live tasks can run against a partially-initialized
                # extension object. Never crash the send loop on missing attributes.
                ps = getattr(self, "_pending_state", None)
                pi = getattr(self, "_pending_image", None)
                if ps is not None:
                    payload = ps
                    try:
                        self._pending_state = None
                    except Exception:
                        pass
                elif pi is not None:
                    payload = pi
                    try:
                        self._pending_image = None
                    except Exception:
                        pass

            now = time.time()
            # Build a heartbeat scene_state at 10Hz, even if images are pending.
            # This guarantees controls receives fresh `base` and won't mark it stale.
            state_payload = None
            if (now - self._last_state_send_t) >= 0.1:
                self._last_state_send_t = now
                # Prefer physics/dynamic-control pose when available: many humanoid USDs keep the articulation
                # root Xform fixed, so reading USD transforms can make the robot appear "stuck" even while walking.
                bx = by = bz = byaw = 0.0
                used_physics_pose = False
                try:
                    # `_last_base_pose` and `_last_pose_update_t` are written in the physics callback.
                    last_pose = getattr(self, "_last_base_pose", None)
                    last_t = float(getattr(self, "_last_pose_update_t", 0.0))
                    if last_pose and (now - last_t) <= 0.5:
                        bx, by, bz, byaw = float(last_pose[0]), float(last_pose[1]), float(last_pose[2]), float(last_pose[3])
                        used_physics_pose = True
                except Exception:
                    used_physics_pose = False

                if not used_physics_pose:
                    # Fallback: read base pose from USD.
                    #
                    # IMPORTANT: prefer the robot *root* prim. Some Unitree USDs include a `pelvis` prim whose
                    # transform sits near the ground (~0.07m) and is not appropriate for "upright" checks.
                    try:
                        cfg = self._read_cfg()
                        stage = omni.usd.get_context().get_stage()
                        prim = None
                        if stage is not None:
                            root = stage.GetPrimAtPath(cfg.prim_path)
                            pelvis = stage.GetPrimAtPath(f"{cfg.prim_path}/pelvis")
                            prim = root if (root and root.IsValid()) else (pelvis if (pelvis and pelvis.IsValid()) else None)
                        if prim and prim.IsValid():
                            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
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
                state_payload = {
                    "type": "scene_state",
                    "robot_kind": "humanoid",
                    "phase": "dream" if bool(getattr(self, "_dream_preview", False)) else "live",
                    "embodiment": getattr(self, "_embodiment", None),
                    "world": getattr(self, "_world_spec", None),
                    "base": {"x": float(bx), "y": float(by), "z": float(bz), "yaw": float(byaw), "vx": float(cmd[0]), "vy": float(cmd[1]), "wz": float(cmd[2])},
                    "objects": {},
                    "sensors": sensors,
                }

            # Send scene_state first (if due), then any pending payload (image/state).
            if state_payload is not None:
                try:
                    # Avoid double-send if the pending payload is already a scene_state.
                    if not (isinstance(payload, dict) and str(payload.get("type") or "") == "scene_state"):
                        await ws.send(json.dumps(state_payload))
                except Exception as e:
                    carb.log_warn(f"[gil.h1_maze_walker] ws send scene_state failed: {e!r}")
                    raise

            # If no physics-driven payload is pending, still publish images at low rate (works while paused).
            # IMPORTANT: keep this *after* the scene_state heartbeat so slow camera capture can't starve base updates.
            capture_enabled = True
            try:
                s = carb.settings.get_settings()
                v = s.get("/gil/camera/capture_enabled")
                capture_enabled = True if v is None else bool(v)
            except Exception:
                capture_enabled = True

            if capture_enabled and payload is None and (now - self._last_image_send_t) >= 1.0:
                self._last_image_send_t = now
                img_main = ""
                img_wide = ""
                extra_images: dict[str, str] = {}
                try:
                    import numpy as _np

                    try:
                        self._maybe_attach_extra_cameras()
                    except Exception:
                        pass

                    async def _grab(annot, rp_path: str | None, *, advance: bool):
                        if annot is None:
                            return None
                        if advance and rp_path:
                            try:
                                import omni.syntheticdata.sensors as sds

                                await sds.next_render_simulation_async(rp_path, 1)
                            except Exception:
                                # Fallback: Replicator may not be driven by syntheticdata in some kit configs.
                                # Step the Replicator orchestrator so annotators produce fresh frames.
                                try:
                                    rep0 = getattr(self, "_rep", None)
                                    orch0 = getattr(rep0, "orchestrator", None) if rep0 is not None else None
                                    if orch0 is not None:
                                        orch0.step()
                                except Exception:
                                    pass
                                await asyncio.sleep(0.01)
                        try:
                            d = annot.get_data()
                        except Exception:
                            return None
                        if d is None:
                            return None
                        a = _np.asarray(d.get("data") if isinstance(d, dict) and "data" in d else d)
                        if a.size <= 0:
                            return None
                        return d

                    raw_fpv = await _grab(self._rgb_annot_fpv, self._rp_fpv_path, advance=True)
                    raw_tpv = await _grab(self._rgb_annot_tpv, self._rp_tpv_path, advance=bool(self._rp_tpv_path))
                    extra_raw: list[tuple[str, Any]] = []
                    for cp, annot in list(self._rgb_by_cam.items()):
                        rp = (self._rp_by_cam or {}).get(cp)
                        raw = await _grab(annot, rp, advance=False)
                        label = str(cp).rsplit("/", 1)[-1]
                        extra_raw.append((label, raw))
                    s = carb.settings.get_settings()
                    fpv_rot = s.get("/gil/camera/fpv_image_rotate_deg")
                    wide_rot = s.get("/gil/camera/wide_image_rotate_deg")
                    fpv_rot = 0.0 if fpv_rot is None else float(fpv_rot)
                    wide_rot = 0.0 if wide_rot is None else float(wide_rot)
                    if raw_fpv is not None:
                        img_main = self._encode_jpeg_data_url(raw_fpv, rotate_deg=fpv_rot)
                    if raw_tpv is not None:
                        img_wide = self._encode_jpeg_data_url(raw_tpv, rotate_deg=wide_rot)
                    for label, raw in extra_raw:
                        if raw is None:
                            continue
                        extra_images[label] = self._encode_jpeg_data_url(raw)

                    collage_tiles: list[tuple[str, Any]] = []
                    if raw_fpv is not None:
                        collage_tiles.append(("fpv", raw_fpv))
                    if raw_tpv is not None:
                        collage_tiles.append(("wide", raw_tpv))
                    collage_tiles.extend(extra_raw)
                    if self._collage_enabled() and collage_tiles:
                        coll = self._compose_camera_collage(collage_tiles, rotate_deg=fpv_rot)
                        if coll:
                            img_main = coll

                    if not self._img_debug_logged:
                        self._img_debug_logged = True
                        carb.log_warn(
                            f"[gil.h1_maze_walker] collage_tiles={ [t[0] for t in collage_tiles] } extra={list(extra_images.keys())}"
                        )
                except Exception:
                    img_main = ""
                    img_wide = ""
                    extra_images = {}
                if img_main or img_wide:
                    payload = {
                        "type": "scene_image",
                        "robot_kind": "humanoid",
                        "image": img_main,
                        "image_wide": img_wide,
                        "images": extra_images,
                        "objects": {},
                        "camera_info": {"collage": bool(self._collage_enabled()), "tiles": list(extra_images.keys())},
                        "camera_info_wide": None,
                    }

            if payload is not None:
                try:
                    await ws.send(json.dumps(payload))
                except Exception:
                    return

            # Optional GT scene export (sampled; safe even while paused).
            try:
                gt = getattr(self, "_gt_scene", None)
                if gt is not None:
                    await gt.maybe_capture(now=float(now))
            except Exception:
                pass

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
                                m2 = UsdGeom.Xformable(prim2).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
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
                        allow_stall_kin = _carb_bool(allow_stall_kin, False)
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
        """Keep FPV camera aligned to robot base pose (world-space look-at).

        We intentionally drive the camera as a world prim (not a child of pelvis/torso) so we don't inherit
        arbitrary link coordinate frames that can rotate the camera unexpectedly.
        Safe to call even while paused.
        """
        now = time.time()
        if (now - self._last_fpv_update_t) < (1.0 / 30.0):
            return
        self._last_fpv_update_t = now
        def _drive_world_camera(cam_path: str, off_xyz: tuple[float, float, float], pitch_deg: float = 0.0) -> None:
            stage2 = omni.usd.get_context().get_stage()
            if stage2 is None:
                return
            prim = stage2.GetPrimAtPath(cam_path)
            if not (prim and prim.IsValid()):
                return
            xf2 = UsdGeom.Xformable(prim)
            try:
                ops2 = xf2.GetOrderedXformOps()
            except Exception:
                ops2 = []
            xlate2 = None
            orient2 = None
            for op in ops2:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    xlate2 = op
                if op.GetOpType() == UsdGeom.XformOp.TypeOrient:
                    orient2 = op
            if xlate2 is None or orient2 is None:
                try:
                    xf2.ClearXformOpOrder()
                except Exception:
                    pass
                xlate2 = xf2.AddTranslateOp()
                orient2 = xf2.AddOrientOp()

            ox2, oy2, oz2 = float(off_xyz[0]), float(off_xyz[1]), float(off_xyz[2])
            cy2 = math.cos(yaw)
            sy2 = math.sin(yaw)
            wx2 = float(rx + (cy2 * ox2 - sy2 * oy2))
            wy2 = float(ry + (sy2 * ox2 + cy2 * oy2))
            wz2 = float(rz + oz2)

            dist2 = 2.0
            dz2 = math.tan(float(pitch_deg) * math.pi / 180.0) * dist2
            tx2 = float(wx2 + cy2 * dist2)
            ty2 = float(wy2 + sy2 * dist2)
            tz2 = float(wz2 + dz2)

            from pxr import Gf

            eye2 = Gf.Vec3d(float(wx2), float(wy2), float(wz2))
            tgt2 = Gf.Vec3d(float(tx2), float(ty2), float(tz2))
            up2 = Gf.Vec3d(0.0, 0.0, 1.0)
            zaxis2 = eye2 - tgt2
            try:
                zaxis2 = zaxis2.GetNormalized()
            except Exception:
                return
            xaxis2 = Gf.Cross(up2, zaxis2)
            try:
                xaxis2 = xaxis2.GetNormalized()
            except Exception:
                xaxis2 = Gf.Vec3d(1.0, 0.0, 0.0)
            yaxis2 = Gf.Cross(zaxis2, xaxis2)

            m00, m10, m20 = float(xaxis2[0]), float(xaxis2[1]), float(xaxis2[2])
            m01, m11, m21 = float(yaxis2[0]), float(yaxis2[1]), float(yaxis2[2])
            m02, m12, m22 = float(zaxis2[0]), float(zaxis2[1]), float(zaxis2[2])
            tr2 = m00 + m11 + m22
            if tr2 > 0.0:
                s4 = (tr2 + 1.0) ** 0.5 * 2.0
                qw2 = 0.25 * s4
                qx2 = (m21 - m12) / s4
                qy2 = (m02 - m20) / s4
                qz2 = (m10 - m01) / s4
            elif (m00 > m11) and (m00 > m22):
                s4 = (1.0 + m00 - m11 - m22) ** 0.5 * 2.0
                qw2 = (m21 - m12) / s4
                qx2 = 0.25 * s4
                qy2 = (m01 + m10) / s4
                qz2 = (m02 + m20) / s4
            elif m11 > m22:
                s4 = (1.0 + m11 - m00 - m22) ** 0.5 * 2.0
                qw2 = (m02 - m20) / s4
                qx2 = (m01 + m10) / s4
                qy2 = 0.25 * s4
                qz2 = (m12 + m21) / s4
            else:
                s4 = (1.0 + m22 - m00 - m11) ** 0.5 * 2.0
                qw2 = (m10 - m01) / s4
                qx2 = (m02 + m20) / s4
                qy2 = (m12 + m21) / s4
                qz2 = 0.25 * s4

            xlate2.Set((float(wx2), float(wy2), float(wz2)))
            orient2.Set(Gf.Quatf(float(qw2), Gf.Vec3f(float(qx2), float(qy2), float(qz2))))

        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            s = carb.settings.get_settings()
            ox = s.get("/gil/camera/fpv_x")
            oy = s.get("/gil/camera/fpv_y")
            oz = s.get("/gil/camera/fpv_z")
            ox = 0.25 if ox is None else float(ox)
            oy = 0.00 if oy is None else float(oy)
            oz = 1.40 if oz is None else float(oz)
            pitch_off = s.get("/gil/camera/fpv_pitch_offset_deg")
            pitch_off = 0.0 if pitch_off is None else float(pitch_off)
            try:
                if (now - float(getattr(self, "_dbg_last_cam_params_t", 0.0))) >= 3.0:
                    self._dbg_last_cam_params_t = now
                    carb.log_warn(
                        f"[gil.h1_maze_walker] fpv_params base=({rx:.2f},{ry:.2f},{rz:.2f}) yaw={yaw:.3f} "
                        f"off=({ox:.2f},{oy:.2f},{oz:.2f}) pitch_off={pitch_off:.1f}"
                    )
            except Exception:
                pass
            _drive_world_camera("/World/Humanoid/FPVCamera", (ox, oy, oz), pitch_deg=float(pitch_off))
            # Isaac Lab H1 maze ChaseCamera (world offset from root).
            cx = s.get("/gil/camera/chase_x")
            cy = s.get("/gil/camera/chase_y")
            cz = s.get("/gil/camera/chase_z")
            cx = -2.5 if cx is None else float(cx)
            cy = 0.0 if cy is None else float(cy)
            cz = 1.6 if cz is None else float(cz)
            _drive_world_camera("/World/Humanoid/ChaseCamera", (cx, cy, cz), pitch_deg=-8.0)
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
            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
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
                if abs(float(yaw_err)) > float(cfg.turn_in_place_err):
                    vx = float(cfg.vx) * float(max(0.0, min(1.0, float(cfg.turn_min_vx_scale))))
                else:
                    vx = float(cfg.vx)
                cmd = np.array([vx, 0.0, wz], dtype=np.float32)

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
            if self._gt_task is not None:
                self._gt_task.cancel()
        except Exception:
            pass
        try:
            if getattr(self, "_task", None) is not None:
                self._task.cancel()
        except Exception:
            pass


class _GtSceneExporter:
    """
    Export a WorldSculpt-compatible GT scene directory using Replicator annotators.

    Layout (scene_dir):
      - 000.png, 001.png, ... RGB frames
      - transforms.json with camera intrinsics + frames[].transform_matrix (c2w, Blender/OpenGL)
        and instances[].pass_index + instances[].aabb_world + masks mapping.
      - masks/objNN/NNNN.png per-instance binary masks (objNN <-> pass_index NN)
    """

    def __init__(self, ext: Extension, *, rp_fpv: Any, camera_prim_path: str, wh: tuple[int, int]) -> None:
        self._ext = ext
        self._rep = getattr(ext, "_rep", None)
        if self._rep is None:
            raise RuntimeError("Replicator not available")

        self._rp = rp_fpv
        self._camera_prim_path = str(camera_prim_path)
        self._w, self._h = int(wh[0]), int(wh[1])

        s = carb.settings.get_settings()
        base = s.get("/gil/gt_scene/output_dir")
        if base is None or not str(base).strip():
            base = os.path.join(os.getcwd(), "assets", "gt_scenes")
        run_id = s.get("/gil/gt_scene/run_id")
        if run_id is None or not str(run_id).strip():
            run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.scene_dir = os.path.join(str(base), str(run_id))
        os.makedirs(self.scene_dir, exist_ok=True)
        # We record "raw" arrays inside Isaac (no PIL dependency), then convert on the host
        # into WorldSculpt's PNG/masks layout.
        self.raw_dir = os.path.join(self.scene_dir, "_raw")
        self.raw_rgb_dir = os.path.join(self.raw_dir, "rgb_npy")
        self.raw_inst_dir = os.path.join(self.raw_dir, "instance_id_npy")
        self.raw_bbox_dir = os.path.join(self.raw_dir, "bbox3d_json")
        os.makedirs(self.raw_rgb_dir, exist_ok=True)
        os.makedirs(self.raw_inst_dir, exist_ok=True)
        os.makedirs(self.raw_bbox_dir, exist_ok=True)

        self.max_frames = int(s.get("/gil/gt_scene/max_frames") or 60)
        self.sample_hz = float(s.get("/gil/gt_scene/sample_hz") or 2.0)
        self.max_instances = int(s.get("/gil/gt_scene/max_instances") or 32)
        self.min_pixels = int(s.get("/gil/gt_scene/min_pixels") or 200)

        rep = self._rep
        ann = rep.AnnotatorRegistry

        def _pick(names: list[str]):
            for n in names:
                try:
                    return ann.get_annotator(n)
                except Exception:
                    continue
            return None

        self._rgb = getattr(ext, "_rgb_annot_fpv", None)
        self._inst = _pick(["instance_id_segmentation", "instance_segmentation", "instance_segmentation_fast"])
        self._bbox3d = _pick(["bounding_box_3d", "bounding_box_3d_fast", "bbox_3d"])

        try:
            if self._inst is not None:
                self._inst.attach([rp_fpv])
        except Exception:
            pass
        try:
            if self._bbox3d is not None:
                self._bbox3d.attach([rp_fpv])
        except Exception:
            pass

        self._t_last = 0.0
        self._frame_idx = 0
        self._frames_meta: list[dict[str, Any]] = []

        # instance_id -> pass_index (0..)
        self._pass_index_by_iid: dict[int, int] = {}
        # pass_index -> (min,max) world AABB
        self._aabb_by_pidx: dict[int, tuple[np.ndarray, np.ndarray]] = {}

        self._intr = self._compute_intrinsics()
        self.is_complete: bool = False

        try:
            carb.log_warn(
                f"[gil.h1_maze_walker] GT export enabled: dir={self.scene_dir} "
                f"max_frames={self.max_frames} hz={self.sample_hz} max_instances={self.max_instances}"
            )
        except Exception:
            pass

    def _compute_intrinsics(self) -> dict[str, float]:
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            w = float(self._w)
            h = float(self._h)
            return {"w": w, "h": h, "fl_x": w, "fl_y": h, "cx": w / 2.0, "cy": h / 2.0}
        prim = stage.GetPrimAtPath(self._camera_prim_path)
        w = float(self._w)
        h = float(self._h)
        try:
            cam = UsdGeom.Camera(prim)
            fl_mm = float(cam.GetFocalLengthAttr().Get() or 24.0)
            hap_mm = float(cam.GetHorizontalApertureAttr().Get() or 20.955)
            vap_mm = float(cam.GetVerticalApertureAttr().Get() or 15.2908)
            fl_x = (fl_mm / max(1e-6, hap_mm)) * w
            fl_y = (fl_mm / max(1e-6, vap_mm)) * h
            cx = w / 2.0
            cy = h / 2.0
            return {"w": w, "h": h, "fl_x": float(fl_x), "fl_y": float(fl_y), "cx": float(cx), "cy": float(cy)}
        except Exception:
            return {"w": w, "h": h, "fl_x": w, "fl_y": h, "cx": w / 2.0, "cy": h / 2.0}

    def _camera_c2w_blender(self) -> list[list[float]]:
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return np.eye(4, dtype=np.float64).tolist()
        prim = stage.GetPrimAtPath(self._camera_prim_path)
        if not (prim and prim.IsValid()):
            return np.eye(4, dtype=np.float64).tolist()
        try:
            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            a = np.array(m, dtype=np.float64)
            if a.shape == (4, 4):
                return a.tolist()
        except Exception:
            pass
        return np.eye(4, dtype=np.float64).tolist()

    def _rgb_to_uint8(self, rgb_raw: Any) -> np.ndarray | None:
        try:
            d = rgb_raw
            if isinstance(d, dict):
                if d.get("data", None) is not None:
                    d = d["data"]
                elif d.get("rgb", None) is not None:
                    d = d["rgb"]
                elif d.get("buffer", None) is not None:
                    d = d["buffer"]
            arr = np.asarray(d)
            if arr.dtype.kind == "f":
                arr = np.clip(arr * 255.0, 0.0, 255.0).astype("uint8")
            else:
                arr = arr.astype("uint8", copy=False)
            # Some builds return flattened buffers; reshape using our configured H,W when possible.
            if arr.ndim == 1:
                n = int(arr.size)
                hw = int(self._h * self._w)
                if hw > 0 and n in (hw * 3, hw * 4):
                    arr = arr.reshape((self._h, self._w, int(n // hw)))
            if arr.ndim == 3 and arr.shape[2] >= 3:
                arr = arr[:, :, :3]
            if arr.size == 0:
                return None
            return arr
        except Exception:
            return None

    def _inst_ids_from_seg(self, seg_raw: Any) -> np.ndarray | None:
        try:
            d = seg_raw
            if isinstance(d, dict):
                if d.get("data", None) is not None:
                    d = d["data"]
                elif d.get("instanceId", None) is not None:
                    d = d["instanceId"]
                elif d.get("instance_id", None) is not None:
                    d = d["instance_id"]
                elif d.get("buffer", None) is not None:
                    d = d["buffer"]
            arr = np.asarray(d)
            if arr.size == 0:
                return None
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            return arr.astype("int64", copy=False)
        except Exception:
            return None

    def _ensure_instance(self, iid: int) -> int | None:
        if iid <= 0:
            return None
        if iid in self._pass_index_by_iid:
            return self._pass_index_by_iid[iid]
        if len(self._pass_index_by_iid) >= self.max_instances:
            return None
        pidx = len(self._pass_index_by_iid)
        self._pass_index_by_iid[iid] = pidx
        os.makedirs(os.path.join(self.scene_dir, "masks", f"obj{pidx:02d}"), exist_ok=True)
        return pidx

    def _update_aabb_from_bbox3d(self, bbox_raw: Any) -> None:
        try:
            d = bbox_raw.get("data") if isinstance(bbox_raw, dict) and "data" in bbox_raw else bbox_raw
            if d is None:
                return
            items = None
            if isinstance(d, list):
                items = d
            elif isinstance(d, dict):
                iids = d.get("instanceId") or d.get("instance_ids") or d.get("ids")
                mins = d.get("aabb_min") or d.get("min") or d.get("mins")
                maxs = d.get("aabb_max") or d.get("max") or d.get("maxs")
                if iids is not None and mins is not None and maxs is not None:
                    items = []
                    for iid, mn, mx in zip(iids, mins, maxs):
                        items.append({"instanceId": int(iid), "aabb_min": mn, "aabb_max": mx})
            if not items:
                return
            for it in items:
                try:
                    iid = int(it.get("instanceId") or it.get("instance_id") or it.get("id"))
                except Exception:
                    continue
                if iid <= 0:
                    continue
                pidx = self._pass_index_by_iid.get(iid)
                if pidx is None:
                    continue
                try:
                    mn = np.asarray(it.get("aabb_min") or it.get("min"), dtype=np.float64).reshape(3)
                    mx = np.asarray(it.get("aabb_max") or it.get("max"), dtype=np.float64).reshape(3)
                except Exception:
                    continue
                if not np.isfinite(mn).all() or not np.isfinite(mx).all():
                    continue
                if pidx not in self._aabb_by_pidx:
                    self._aabb_by_pidx[pidx] = (mn.copy(), mx.copy())
                else:
                    a0, a1 = self._aabb_by_pidx[pidx]
                    self._aabb_by_pidx[pidx] = (np.minimum(a0, mn), np.maximum(a1, mx))
        except Exception:
            return

    async def maybe_capture(self, *, now: float) -> None:
        if self._frame_idx >= self.max_frames:
            return
        if self.sample_hz > 0 and (now - self._t_last) < (1.0 / self.sample_hz):
            return
        self._t_last = float(now)

        # Force a render tick for fresh annotator buffers.
        stepped = False
        try:
            orch0 = getattr(self._rep, "orchestrator", None)
            if orch0 is not None and hasattr(orch0, "step_async"):
                await orch0.step_async(rt_subframes=1)
                stepped = True
        except Exception:
            stepped = False
        if not stepped:
            try:
                rp_path = getattr(self._ext, "_rp_fpv_path", None)
                if rp_path:
                    import omni.syntheticdata.sensors as sds

                    await sds.next_render_simulation_async(rp_path, 1)
                    stepped = True
            except Exception:
                stepped = False
        if not stepped:
            try:
                orch0 = getattr(self._rep, "orchestrator", None)
                if orch0 is not None:
                    orch0.step()
            except Exception:
                pass
        # Give SDG/replicator a moment to populate host buffers.
        try:
            await asyncio.sleep(0.02)
        except Exception:
            pass

        rgb_raw = None
        try:
            if self._rgb is not None:
                rgb_raw = self._rgb.get_data()
        except Exception:
            rgb_raw = None
        if rgb_raw is None:
            return

        inst_raw = None
        inst_ids = None
        if self._inst is not None:
            try:
                inst_raw = self._inst.get_data()
                inst_ids = self._inst_ids_from_seg(inst_raw)
            except Exception:
                inst_raw = None
                inst_ids = None

        # Allocate pass indices for the largest visible instances (bounded).
        if inst_ids is not None:
            try:
                uniq, counts = np.unique(inst_ids, return_counts=True)
                pairs = [(int(i), int(c)) for (i, c) in zip(uniq.tolist(), counts.tolist()) if int(i) > 0 and int(c) >= self.min_pixels]
                pairs.sort(key=lambda x: -x[1])
                budget = max(0, self.max_instances - len(self._pass_index_by_iid))
                for iid, _ in pairs[:budget]:
                    self._ensure_instance(int(iid))
            except Exception:
                pass

        # Save raw RGB frame as .npy (host conversion will emit PNG).
        rgb_u8 = self._rgb_to_uint8(rgb_raw)
        frame_stem = f"{self._frame_idx:04d}"
        if rgb_u8 is not None:
            try:
                np.save(os.path.join(self.raw_rgb_dir, f"{frame_stem}.npy"), rgb_u8)
            except Exception:
                pass
        else:
            # Debug breadcrumbs (first frame only): capture raw schema so we can adapt parsing.
            if self._frame_idx == 0:
                try:
                    import json as _json

                    with open(os.path.join(self.raw_dir, "rgb_raw_debug.json"), "w", encoding="utf-8") as f:
                        _json.dump(rgb_raw, f, indent=2, default=str)
                except Exception:
                    pass

        # Save raw instance-id segmentation (host conversion will emit per-instance PNG masks).
        if inst_ids is not None:
            try:
                np.save(os.path.join(self.raw_inst_dir, f"{frame_stem}.npy"), inst_ids.astype("int64", copy=False))
            except Exception:
                pass
        else:
            if self._frame_idx == 0 and self._inst is not None:
                try:
                    import json as _json

                    inst_raw = self._inst.get_data()
                    with open(os.path.join(self.raw_dir, "instance_raw_debug.json"), "w", encoding="utf-8") as f:
                        _json.dump(inst_raw, f, indent=2, default=str)
                except Exception:
                    pass

        # Persist instance id->label mapping once (frame 0) for host-side association with bbox primPaths.
        if self._frame_idx == 0 and isinstance(inst_raw, dict) and isinstance(inst_raw.get("info"), dict):
            try:
                import json as _json

                with open(os.path.join(self.raw_dir, "instance_info.json"), "w", encoding="utf-8") as f:
                    _json.dump(inst_raw.get("info") or {}, f, indent=2, default=str)
            except Exception:
                pass

        # Persist raw bbox3d annotator output for host-side parsing/debug.
        if self._bbox3d is not None:
            try:
                import json as _json

                bbox_raw = self._bbox3d.get_data()
                with open(os.path.join(self.raw_bbox_dir, f"{frame_stem}.json"), "w", encoding="utf-8") as f:
                    _json.dump(bbox_raw, f, indent=2, default=str)
                if self._frame_idx == 0 and isinstance(bbox_raw, dict) and isinstance(bbox_raw.get("info"), dict):
                    with open(os.path.join(self.raw_dir, "bbox_info.json"), "w", encoding="utf-8") as f:
                        _json.dump(bbox_raw.get("info") or {}, f, indent=2, default=str)
            except Exception:
                pass

        # Record frame pose (c2w in Blender/OpenGL convention).
        self._frames_meta.append({"frame": int(self._frame_idx), "transform_matrix": self._camera_c2w_blender()})
        self._frame_idx += 1

        if self._frame_idx >= self.max_frames:
            self._finalize()

    def _finalize(self) -> None:
        try:
            meta: dict[str, Any] = dict(self._intr)
            meta["camera_model"] = "OPENGL"
            meta["frames"] = list(self._frames_meta)
            meta["raw"] = {
                "rgb_npy": "_raw/rgb_npy",
                "instance_id_npy": "_raw/instance_id_npy",
                "bbox3d_json": "_raw/bbox3d_json",
            }
            outp = os.path.join(self.raw_dir, "meta.json")
            with open(outp, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            with open(os.path.join(self.scene_dir, "_DONE.txt"), "w", encoding="utf-8") as f:
                f.write("ok\n")
            carb.log_warn(f"[gil.h1_maze_walker] GT export complete (raw): {outp}")
            self.is_complete = True
        except Exception as e:
            try:
                carb.log_warn(f"[gil.h1_maze_walker] GT export finalize failed: {e!r}")
            except Exception:
                pass


