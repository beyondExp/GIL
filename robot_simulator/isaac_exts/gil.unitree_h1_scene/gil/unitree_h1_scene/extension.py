import omni.ext
import carb
import omni.usd
import omni.client
import asyncio
import os
import json
import omni.kit.app
from pxr import Gf
from pxr import Usd, UsdGeom

from .sensor_attachment import ensure_full_humanoid_sensors, AttachedSensorPrims


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        carb.log_info(f"[gil.unitree_h1_scene] startup ext_id={ext_id}")
        try:
            # Kit runs an asyncio loop internally; ensure_future schedules on it.
            self._task = asyncio.ensure_future(self._load_when_ready())
            self._follow_task = None
            self._attached_sensors: AttachedSensorPrims | None = None
            self._attach_sensors_task: asyncio.Task | None = None
        except Exception as e:
            carb.log_error(f"[gil.unitree_h1_scene] Failed to start async loader: {e!r}")
            self._task = None
            self._follow_task = None
            self._attached_sensors = None
            self._attach_sensors_task = None

    async def _attach_sensor_prims_when_ready(self, dt: float) -> None:
        """
        Ensure IMU/contact *prim paths* exist under the humanoid, without instantiating sensor wrappers.

        We intentionally avoid constructing `IMUSensor` / `ContactSensor` Python wrapper objects here because
        they can trigger PhysX tensor/simulation-view initialization while Kit is still booting or while the
        stage is being rebuilt, which can invalidate tensor views and freeze streaming sessions.
        """
        app = omni.kit.app.get_app()
        # Wait up to ~2 minutes (streaming/asset load can be slow on first run)
        for _ in range(1200):
            try:
                stage = omni.usd.get_context().get_stage()
                root = None if stage is None else stage.GetPrimAtPath("/World/Humanoid")
                if root and root.IsValid():
                    break
            except Exception:
                pass
            await app.next_update_async()
        else:
            carb.log_warn("[gil.unitree_h1_scene] Timed out waiting for humanoid prim; skipping sensor prim attachment.")
            return

        # A few retries in case asset payloads are still streaming in for collision prim discovery.
        for attempt in range(10):
            try:
                self._attached_sensors = ensure_full_humanoid_sensors(root_prim_path="/World/Humanoid", dt=dt)
                try:
                    s = carb.settings.get_settings()
                    if self._attached_sensors and self._attached_sensors.imu_prim_path:
                        s.set("/gil/sensors/imu_prim_path", self._attached_sensors.imu_prim_path)
                    if self._attached_sensors and self._attached_sensors.contacts:
                        s.set("/gil/sensors/contact_prim_paths_json", json.dumps(self._attached_sensors.contacts))
                except Exception:
                    pass
                return
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Sensor prim attach attempt {attempt+1}/10 failed: {e!r}")
                await asyncio.sleep(0.25)
                await app.next_update_async()

    async def _load_when_ready(self) -> None:
        try:
            # Isaac Sim sometimes starts extensions before a USD stage is fully ready.
            # Retry for a while rather than failing hard.
            app = omni.kit.app.get_app()
            for _ in range(600):  # ~60s (one check per update)
                stage = omni.usd.get_context().get_stage()
                if stage is not None:
                    break
                await app.next_update_async()
            else:
                carb.log_error("[gil.unitree_h1_scene] No USD stage available after waiting")
                return
            stage = omni.usd.get_context().get_stage()

            # Ensure consistent units/axis so assets don't appear scaled incorrectly.
            try:
                from pxr import UsdGeom

                UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
                UsdGeom.SetStageMetersPerUnit(stage, 1.0)
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to set stage units/axis: {e!r}")

            try:
                # Isaac Sim native asset root (e.g. omniverse content pack)
                from isaacsim.storage.native import get_assets_root_path
                import isaacsim.core.utils.stage as stage_utils
            except Exception as e:
                carb.log_error(f"[gil.unitree_h1_scene] Isaac Sim APIs not available: {e!r}")
                return

            assets_root = get_assets_root_path()
            if not assets_root:
                carb.log_error("[gil.unitree_h1_scene] Could not find Isaac Sim assets root path")
                return

            env_usd = assets_root + "/Isaac/Environments/Grid/default_environment.usd"

            def _exists(url: str) -> bool:
                try:
                    res, _ = omni.client.stat(url)
                    return res == omni.client.Result.OK
                except Exception:
                    return False

            # Allow overriding the robot selection (relative to assets_root).
            # - Kit setting: /gil/humanoid/usd_rel
            # - Env var: GIL_HUMANOID_USD_REL
            settings = carb.settings.get_settings()
            desired_rel = settings.get("/gil/humanoid/usd_rel") or os.environ.get("GIL_HUMANOID_USD_REL", "")
            desired_rel = str(desired_rel).strip()

            # Optional: prefer local Unitree H1-2 asset pack shipped in this repo (IsaacLab third-party).
            # This makes "try H1-2 first" trivial while keeping default behavior unchanged unless enabled.
            prefer_h12 = settings.get("/gil/humanoid/prefer_h1_2")
            prefer_h12 = bool(prefer_h12) if prefer_h12 is not None else (str(os.environ.get("GIL_PREFER_H1_2", "")).strip().lower() in ("1", "true", "yes", "on"))
            local_h12_usd = ""
            if prefer_h12 and (not desired_rel):
                try:
                    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
                    cand = os.path.join(
                        repo_root,
                        "_third_party",
                        "unitree_sim_isaaclab",
                        "assets",
                        "robots",
                        "h1_2-26dof-inspire-base-fix-usd",
                        "h1_2_26dof_with_inspire_rev_1_0.usd",
                    )
                    if os.path.exists(cand):
                        local_h12_usd = cand
                except Exception:
                    local_h12_usd = ""

            candidates_rel = [
                # Official-ish H1-2 paths (only used if they exist in the Isaac assets root)
                "/Isaac/Robots/Unitree/H1_2/h1_2.usd",
                "/Isaac/Robots/Unitree/H1_2/h1_2_robot.usd",
                "/Isaac/Robots/Unitree/H1-2/h1-2.usd",
                # Prefer "not H1" humanoids first if present
                "/Isaac/Robots/Unitree/G1/g1.usd",
                "/Isaac/Robots/Unitree/G1/g1_robot.usd",
                "/Isaac/Robots/Unitree/G1/unitree_g1.usd",
                "/Isaac/Robots/NVIDIA/Humanoid/humanoid.usd",
                "/Isaac/Robots/IsaacSim/Humanoid/humanoid.usd",
                # Known-good fallback
                "/Isaac/Robots/Unitree/H1/h1.usd",
            ]

            # Optional: force H1 when we want to use the built-in flat-terrain locomotion policy controller.
            prefer_h1 = settings.get("/gil/humanoid/prefer_h1_policy")
            if bool(prefer_h1):
                candidates_rel = ["/Isaac/Robots/Unitree/H1/h1.usd"] + [c for c in candidates_rel if c != "/Isaac/Robots/Unitree/H1/h1.usd"]

            if desired_rel:
                # Support absolute local paths and URLs too (for external asset packs like Unitree H1-2).
                candidates_rel = [desired_rel] + [c for c in candidates_rel if c != desired_rel]
            elif local_h12_usd:
                candidates_rel = [local_h12_usd] + candidates_rel

            chosen_url = ""
            for rel in candidates_rel:
                rel_s = str(rel)
                # Absolute path / URL?
                if "://" in rel_s or os.path.isabs(rel_s) or (len(rel_s) > 2 and rel_s[1] == ":" and (rel_s[2] in ("\\", "/"))):
                    # Local file exists or remote URL is accepted.
                    if ("://" in rel_s) or os.path.exists(rel_s):
                        chosen_url = rel_s
                        break
                    continue
                # Treat as assets_root-relative
                if not rel_s.startswith("/"):
                    rel_s = "/" + rel_s
                url = assets_root + rel_s
                if _exists(url):
                    chosen_url = url
                    break

            if not chosen_url:
                carb.log_warn(
                    "[gil.unitree_h1_scene] No humanoid USD candidates found in assets root; falling back to H1 path anyway"
                )
                chosen_url = assets_root + "/Isaac/Robots/Unitree/H1/h1.usd"

            robot_usd = chosen_url
            carb.log_info(f"[gil.unitree_h1_scene] selected_robot_usd={robot_usd}")

            # Load environment + robot
            try:
                stage_utils.add_reference_to_stage(env_usd, prim_path="/World/Env")
                stage_utils.add_reference_to_stage(robot_usd, prim_path="/World/Humanoid")
            except Exception as e:
                carb.log_error(f"[gil.unitree_h1_scene] Failed to add references: {e!r}")
                return

            # If this is a Unitree H1-2 asset pack, layer the provided "sensor" USD on top of the robot.
            # This is the safest way to get the *exact* cameras/sensors and transforms shipped by Unitree.
            try:
                if os.path.isabs(robot_usd) and os.path.exists(robot_usd):
                    base_dir = os.path.dirname(robot_usd)
                    # Common layout in unitree_sim_isaaclab assets:
                    #   <base_dir>/configuration/h1_2.urdf_sensor.usd
                    sensor_usd = os.path.join(base_dir, "configuration", "h1_2.urdf_sensor.usd")
                    if os.path.exists(sensor_usd):
                        stage_utils.add_reference_to_stage(sensor_usd, prim_path="/World/Humanoid")
                        carb.log_info(f"[gil.unitree_h1_scene] layered_sensor_usd={sensor_usd}")
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to layer sensor USD: {e!r}")

            # Apply robot sensor configuration (camera intrinsics/extrinsics/etc) if provided.
            try:
                self._apply_robot_sensor_config()
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to apply sensor config: {e!r}")

            # Discover any cameras/sensors shipped inside the robot USD and expose them via settings/logs.
            try:
                self._discover_robot_sensors()
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to discover robot sensors: {e!r}")

            # In some Kit/streaming configurations payloads are not loaded by default.
            # Force-load payloads for the whole stage and wait briefly for asset loading to complete.
            try:
                stage.Load()
                for _ in range(600):  # up to ~10s at 60Hz
                    pending = omni.usd.get_context().get_stage_loading_status()[2]
                    if pending <= 0:
                        break
                    await app.next_update_async()
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to stage.Load() payloads: {e!r}")

            # Force-load payloads/visuals for the referenced robot (sometimes visuals are not loaded yet in streaming).
            try:
                from pxr import Usd

                humanoid_prim = stage.GetPrimAtPath("/World/Humanoid")
                if humanoid_prim and humanoid_prim.IsValid():
                    # If the referenced asset is instanceable, uninstance it so we can traverse/override reliably.
                    try:
                        humanoid_prim.SetInstanceable(False)
                    except Exception:
                        pass
                    # Load payloads (visual meshes are usually in payload layers)
                    humanoid_prim.Load()

                    # Some assets attach payloads on each link's `/visuals` prim; explicitly load them.
                    for p in Usd.PrimRange(humanoid_prim):
                        if p.GetName() != "visuals":
                            continue
                        try:
                            p.Load()
                        except Exception:
                            continue
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to force-load humanoid payloads: {e!r}")

            # Ensure there is some light in the scene (streaming/headless sessions can look black otherwise).
            try:
                from pxr import UsdLux, UsdGeom

                UsdGeom.Scope.Define(stage, "/World/Lights")

                dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
                dome.CreateIntensityAttr(800.0)

                sun = UsdLux.DistantLight.Define(stage, "/World/Lights/SunLight")
                sun.CreateIntensityAttr(3000.0)
                # Idempotent rotate op (avoid "already exists" errors on reloads)
                xf = UsdGeom.Xformable(sun.GetPrim())
                rot = None
                for op in xf.GetOrderedXformOps():
                    if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
                        rot = op
                        break
                if rot is None:
                    rot = xf.AddRotateXYZOp()
                rot.Set(Gf.Vec3f(-35.0, 45.0, 0.0))
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to create lights: {e!r}")

            # Position robot (safe-ish spawn height)
            try:
                from pxr import UsdGeom

                prim = stage.GetPrimAtPath("/World/Humanoid")
                if prim and prim.IsValid():
                    # Force visibility on the root (in case the referenced asset is hidden).
                    try:
                        UsdGeom.Imageable(prim).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                    except Exception:
                        pass

                    xf = UsdGeom.Xformable(prim)
                    ops = xf.GetOrderedXformOps()
                    xlate = None
                    for op in ops:
                        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                            xlate = op
                            break
                    if xlate is None:
                        xlate = xf.AddTranslateOp()
                    xlate.Set(Gf.Vec3d(0.0, 0.0, 1.05))

                    # Optional scale override (useful if some sessions still show wrong size).
                    s = carb.settings.get_settings().get("/gil/humanoid/scale")
                    scale_val = 1.0 if s is None else float(s)
                    if abs(scale_val - 1.0) > 1e-6:
                        xf.AddScaleOp().Set(Gf.Vec3f(scale_val, scale_val, scale_val))
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Could not set spawn transform: {e!r}")

            # Make humanoid visuals editable/reliable: some assets ship as USD instances (hard to override).
            # De-instancing the `/visuals` prims keeps the robot visible and makes later experiment overrides possible.
            try:
                from pxr import Usd

                prim = stage.GetPrimAtPath("/World/Humanoid")
                if prim and prim.IsValid():
                    deinstanced = 0
                    for p in Usd.PrimRange(prim):
                        if p.GetName() == "visuals":
                            try:
                                p.SetInstanceable(False)
                                deinstanced += 1
                            except Exception:
                                continue
                    if deinstanced > 0:
                        carb.log_info(f"[gil.unitree_h1_scene] deinstanced_visuals={deinstanced}")
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to de-instance visuals: {e!r}")

            # Add cameras:
            # - FPV camera prim (for recording/sensors)
            # - Follow camera behavior for the streamed viewport camera (/OmniverseKit_Persp)
            # If an H1-2 sensor config is provided, ensure the full camera suite exists first.
            try:
                self._ensure_unitree_camera_suite_from_config()
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to ensure camera suite: {e!r}")
            self._ensure_cameras()
            # Re-run discovery after camera suite setup so we log the final set of cameras/sensors.
            try:
                self._discover_robot_sensors()
            except Exception:
                pass

            # Attach "full" physics sensors (IMU + contacts) to the humanoid, matching Unitree IsaacLab expectations.
            # IMPORTANT: only author sensor prims here; do NOT instantiate wrappers during startup.
            try:
                s = carb.settings.get_settings()
                enable = s.get("/gil/sensors/enable")
                enable = True if enable is None else bool(enable)
                if enable:
                    dt = s.get("/gil/sensors/dt")
                    dt = 0.02 if dt is None else float(dt)
                    if self._attach_sensors_task is None or self._attach_sensors_task.done():
                        self._attach_sensors_task = asyncio.ensure_future(self._attach_sensor_prims_when_ready(dt=dt))
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to schedule sensor attachment: {e!r}")

            carb.log_info("[gil.unitree_h1_scene] Humanoid scene loaded. Press Play in Isaac Sim to start physics.")
            self._set_default_camera()
        except Exception as e:
            carb.log_error(f"[gil.unitree_h1_scene] Unhandled exception in async loader: {e!r}")

    def _discover_robot_sensors(self) -> None:
        """
        Scan the loaded robot prim for any built-in camera/sensor prims.
        If a camera exists and /gil/camera/fpv_prim_path isn't set, pick the first camera as FPV source.
        """
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        root = stage.GetPrimAtPath("/World/Humanoid")
        if not (root and root.IsValid()):
            return

        cameras: list[str] = []
        sensors: list[str] = []
        sensor_schemas: list[str] = []
        for p in Usd.PrimRange(root):
            try:
                if p.IsA(UsdGeom.Camera):
                    cameras.append(p.GetPath().pathString)
                    continue
            except Exception:
                pass
            tn = (p.GetTypeName() or "").lower()
            # Heuristic: Isaac sensor prim types often include these tokens.
            if any(tok in tn for tok in ("sensor", "lidar", "imu", "camera", "depth", "rtx")):
                sensors.append(p.GetPath().pathString)
            # Also look at applied API schemas for sensor-related tokens.
            try:
                for sch in (p.GetAppliedSchemas() or []):
                    sch_s = str(sch)
                    if any(tok in sch_s.lower() for tok in ("sensor", "imu", "contact", "lidar", "camera", "rtx", "isaac")):
                        sensor_schemas.append(f"{p.GetPath().pathString}:{sch_s}")
            except Exception:
                pass

        if cameras:
            carb.log_info(f"[gil.unitree_h1_scene] discovered_cameras={cameras}")
        if sensors:
            carb.log_info(f"[gil.unitree_h1_scene] discovered_sensor_like_prims={sensors[:50]}")
        if sensor_schemas:
            # keep log bounded
            carb.log_info(f"[gil.unitree_h1_scene] discovered_sensor_schemas={sensor_schemas[:80]}")

        s = carb.settings.get_settings()
        fpv_override = s.get("/gil/camera/fpv_prim_path")
        if (fpv_override is None or str(fpv_override).strip() == "") and cameras:
            s.set("/gil/camera/fpv_prim_path", cameras[0])
            carb.log_info(f"[gil.unitree_h1_scene] auto-selected fpv_prim_path={cameras[0]}")

    def _apply_robot_sensor_config(self) -> None:
        """
        Load a JSON sensor calibration file and map it into carb settings that
        our camera/capture pipeline uses.

        Priority:
        - Kit setting: /gil/robot/sensors_config
        - Env var: GIL_ROBOT_SENSORS_CONFIG
        """
        s = carb.settings.get_settings()
        cfg_path = s.get("/gil/robot/sensors_config") or os.environ.get("GIL_ROBOT_SENSORS_CONFIG", "")
        cfg_path = str(cfg_path).strip()
        if not cfg_path:
            return
        if not os.path.exists(cfg_path):
            carb.log_warn(f"[gil.unitree_h1_scene] sensors_config not found: {cfg_path}")
            return

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}

        cams = (cfg.get("cameras") or {})
        # If a full multi-camera suite is provided, we will spawn/ensure those cameras later.
        # Here we just map common settings + choose a default FPV camera.
        wide = (cams.get("wide_debug") or {})

        # Prefer a real front camera if present; else fall back to a legacy "fpv" entry.
        fpv = (cams.get("front_cam") or cams.get("fpv") or {})

        def _set_if(key: str, v) -> None:
            if v is None:
                return
            try:
                s.set(key, v)
            except Exception:
                return

        # Default FPV camera prim selection (front_cam is the "real" one for H1-2)
        fpv_prim_path = fpv.get("prim_path")
        if isinstance(fpv_prim_path, str) and fpv_prim_path.strip():
            _set_if("/gil/camera/fpv_prim_path", fpv_prim_path.strip())

        # FPV resolution (used by capture)
        res = fpv.get("resolution_px")
        if isinstance(res, list) and len(res) == 2:
            _set_if("/gil/camera/fpv_width", int(res[0]))
            _set_if("/gil/camera/fpv_height", int(res[1]))

        # Wide/debug resolution (used for capture, not a physical sensor)
        wres = wide.get("resolution_px")
        if isinstance(wres, list) and len(wres) == 2:
            _set_if("/gil/camera/wide_width", int(wres[0]))
            _set_if("/gil/camera/wide_height", int(wres[1]))

        carb.log_info(f"[gil.unitree_h1_scene] Applied sensors_config={cfg_path}")

    def _ensure_unitree_camera_suite_from_config(self) -> None:
        """
        If /gil/robot/sensors_config points at a JSON with multiple cameras, ensure
        those cameras exist and are attached under the robot at the configured prim paths.
        """
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        s = carb.settings.get_settings()
        cfg_path = s.get("/gil/robot/sensors_config") or os.environ.get("GIL_ROBOT_SENSORS_CONFIG", "")
        cfg_path = str(cfg_path).strip()
        if not cfg_path or not os.path.exists(cfg_path):
            return
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            return

        cams = cfg.get("cameras") or {}
        if not isinstance(cams, dict) or not cams:
            return

        def _quat_wxyz_to_mat3(qwxyz: list[float]) -> list[list[float]]:
            w, x, y, z = [float(v) for v in qwxyz]
            # normalized quaternion to rotation matrix
            n = (w * w + x * x + y * y + z * z) ** 0.5
            if n <= 1e-12:
                return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            w, x, y, z = w / n, x / n, y / n, z / n
            xx, yy, zz = x * x, y * y, z * z
            xy, xz, yz = x * y, x * z, y * z
            wx, wy, wz = w * x, w * y, w * z
            return [
                [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
                [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
                [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
            ]

        def _mat3_to_quat_wxyz(m: list[list[float]]) -> list[float]:
            # robust matrix->quat (wxyz)
            m00, m01, m02 = m[0]
            m10, m11, m12 = m[1]
            m20, m21, m22 = m[2]
            tr = m00 + m11 + m22
            if tr > 0.0:
                s = (tr + 1.0) ** 0.5 * 2.0
                w = 0.25 * s
                x = (m21 - m12) / s
                y = (m02 - m20) / s
                z = (m10 - m01) / s
            elif (m00 > m11) and (m00 > m22):
                s = (1.0 + m00 - m11 - m22) ** 0.5 * 2.0
                w = (m21 - m12) / s
                x = 0.25 * s
                y = (m01 + m10) / s
                z = (m02 + m20) / s
            elif m11 > m22:
                s = (1.0 + m11 - m00 - m22) ** 0.5 * 2.0
                w = (m02 - m20) / s
                x = (m01 + m10) / s
                y = 0.25 * s
                z = (m12 + m21) / s
            else:
                s = (1.0 + m22 - m00 - m11) ** 0.5 * 2.0
                w = (m10 - m01) / s
                x = (m02 + m20) / s
                y = (m12 + m21) / s
                z = 0.25 * s
            return [float(w), float(x), float(y), float(z)]

        # IsaacLab uses CameraCfg.OffsetCfg(convention="ros"). Those quaternions are ROS optical-frame conventions.
        # USD/Omniverse pinhole camera uses the classic OpenGL camera frame (x right, y up, -z forward).
        # ROS optical frame is (x right, y down, z forward). Convert optical -> OpenGL by RotX(pi).
        R_optical_to_opengl = [[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]]

        def _mul3(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
            return [
                [
                    a[0][0] * b[0][j] + a[0][1] * b[1][j] + a[0][2] * b[2][j]
                    for j in range(3)
                ],
                [
                    a[1][0] * b[0][j] + a[1][1] * b[1][j] + a[1][2] * b[2][j]
                    for j in range(3)
                ],
                [
                    a[2][0] * b[0][j] + a[2][1] * b[1][j] + a[2][2] * b[2][j]
                    for j in range(3)
                ],
            ]

        def _ros_optical_quat_to_usd_camera_quat(qwxyz: list[float]) -> list[float]:
            R_parent_to_opt = _quat_wxyz_to_mat3(qwxyz)
            R_parent_to_gl = _mul3(R_parent_to_opt, R_optical_to_opengl)
            return _mat3_to_quat_wxyz(R_parent_to_gl)

        created: list[str] = []

        def _ensure_camera(cam_cfg: dict) -> None:
            if not isinstance(cam_cfg, dict):
                return
            prim_path = cam_cfg.get("prim_path")
            if not isinstance(prim_path, str) or not prim_path.strip():
                return
            prim_path = prim_path.strip()

            # Make sure the parent link exists; if not, skip (we must not create fake links).
            parent_path = os.path.dirname(prim_path.replace("\\", "/"))
            parent_prim = stage.GetPrimAtPath(parent_path)
            if not (parent_prim and parent_prim.IsValid()):
                carb.log_warn(f"[gil.unitree_h1_scene] Camera parent prim missing, skipping: {prim_path}")
                return

            prim = stage.GetPrimAtPath(prim_path)
            if not (prim and prim.IsValid() and prim.IsA(UsdGeom.Camera)):
                cam = UsdGeom.Camera.Define(stage, prim_path)
                prim = cam.GetPrim()
                created.append(prim_path)
            cam = UsdGeom.Camera(prim)

            # Apply intrinsics
            pin = cam_cfg.get("pinhole") or {}
            try:
                if "focal_length" in pin:
                    cam.CreateFocalLengthAttr().Set(float(pin["focal_length"]))
                if "horizontal_aperture" in pin:
                    cam.CreateHorizontalApertureAttr().Set(float(pin["horizontal_aperture"]))
                if "focus_distance" in pin:
                    cam.CreateFocusDistanceAttr().Set(float(pin["focus_distance"]))
                if "clipping_range" in pin and isinstance(pin["clipping_range"], (list, tuple)) and len(pin["clipping_range"]) == 2:
                    cam.CreateClippingRangeAttr().Set((float(pin["clipping_range"][0]), float(pin["clipping_range"][1])))
            except Exception:
                pass

            # Apply mount offset relative to parent
            mount = cam_cfg.get("mount") or {}
            pos = mount.get("pos_m")
            quat = mount.get("rot_quat_wxyz")
            if isinstance(pos, list) and len(pos) == 3 and isinstance(quat, list) and len(quat) == 4:
                # Convert ROS optical quaternion into USD/OpenGL camera quaternion before authoring.
                quat = _ros_optical_quat_to_usd_camera_quat([float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])])
                xf = UsdGeom.Xformable(prim)
                # Ensure ops exist
                xlate = None
                orient = None
                for op in xf.GetOrderedXformOps():
                    if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                        xlate = op
                    if op.GetOpType() == UsdGeom.XformOp.TypeOrient:
                        orient = op
                if xlate is None:
                    xlate = xf.AddTranslateOp()
                if orient is None:
                    orient = xf.AddOrientOp()
                xlate.Set(Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2])))
                orient.Set(Gf.Quatf(float(quat[0]), Gf.Vec3f(float(quat[1]), float(quat[2]), float(quat[3]))))

        # Ensure all declared cameras (skip wide_debug because it's a viewport camera).
        for name, cam_cfg in cams.items():
            if name == "wide_debug":
                continue
            _ensure_camera(cam_cfg)

        # Prefer front_cam as FPV if present
        if "front_cam" in cams and isinstance(cams["front_cam"], dict) and isinstance(cams["front_cam"].get("prim_path"), str):
            s.set("/gil/camera/fpv_prim_path", str(cams["front_cam"]["prim_path"]))

        if created:
            carb.log_info(f"[gil.unitree_h1_scene] ensured_cameras={created}")

        # Log camera world pose directions for debugging transform correctness.
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                for name, cam_cfg in cams.items():
                    if name == "wide_debug":
                        continue
                    pth = cam_cfg.get("prim_path") if isinstance(cam_cfg, dict) else None
                    if not isinstance(pth, str) or not pth:
                        continue
                    prim = stage.GetPrimAtPath(pth)
                    if not (prim and prim.IsValid() and prim.IsA(UsdGeom.Camera)):
                        continue
                    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0.0)
                    t = m.ExtractTranslation()
                    fwd = m.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))  # USD camera forward
                    up = m.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
                    carb.log_info(
                        f"[gil.unitree_h1_scene] cam_pose name={name} path={pth} pos=({t[0]:.3f},{t[1]:.3f},{t[2]:.3f}) fwd=({fwd[0]:.3f},{fwd[1]:.3f},{fwd[2]:.3f}) up=({up[0]:.3f},{up[1]:.3f},{up[2]:.3f})"
                    )
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] cam_pose log failed: {e!r}")

    def _ensure_cameras(self) -> None:
        """
        Create an FPV camera under /World/Humanoid and optionally enable a follow-cam behavior
        that updates the streamed viewport camera (/OmniverseKit_Persp) every frame.

        NOTE: A follow camera can make motion hard to perceive (it tracks the robot).
        We also create a fixed third-person camera for clear visual confirmation of movement.
        """
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return

            from pxr import UsdGeom

            humanoid = stage.GetPrimAtPath("/World/Humanoid")
            if not humanoid or not humanoid.IsValid():
                return

            s = carb.settings.get_settings()
            # Defaults: create both cameras; follow behavior disabled by default (can hide motion).
            follow_enabled = s.get("/gil/camera/follow_enabled")
            follow_enabled = False if follow_enabled is None else bool(follow_enabled)

            # FPV camera: prefer an existing camera shipped inside the robot USD (sensor layer).
            # Only create our synthetic FPVCamera if no camera exists at the chosen path.
            fpv_path = s.get("/gil/camera/fpv_prim_path")
            fpv_path = "/World/Humanoid/FPVCamera" if fpv_path is None else str(fpv_path)
            fpv_prim = stage.GetPrimAtPath(fpv_path)
            created_synth = False
            if not (fpv_prim and fpv_prim.IsValid() and fpv_prim.IsA(UsdGeom.Camera)):
                fpv = UsdGeom.Camera.Define(stage, fpv_path)
                fpv_prim = fpv.GetPrim()
                created_synth = True
            try:
                img = UsdGeom.Imageable(fpv_prim)
                img.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            except Exception:
                pass

            # Intrinsics: only override if explicitly requested OR we created the synthetic FPVCamera.
            try:
                override_intr = s.get("/gil/camera/override_intrinsics")
                override_intr = False if override_intr is None else bool(override_intr)
                if override_intr or created_synth:
                    focal = s.get("/gil/camera/fpv_focal_length_mm")
                    hap = s.get("/gil/camera/fpv_horizontal_aperture_mm")
                    vap = s.get("/gil/camera/fpv_vertical_aperture_mm")
                    cam = UsdGeom.Camera(fpv_prim)
                    cam.CreateFocalLengthAttr().Set(18.0 if focal is None else float(focal))
                    cam.CreateHorizontalApertureAttr().Set(20.955 if hap is None else float(hap))  # ~16:9
                    if vap is not None:
                        cam.CreateVerticalApertureAttr().Set(float(vap))
            except Exception:
                pass

            # Only position/rotate the FPV camera if we created the synthetic FPVCamera.
            if created_synth and fpv_path == "/World/Humanoid/FPVCamera":
                xf = UsdGeom.Xformable(fpv_prim)
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

                # Allow overrides via settings
                fpv_x = s.get("/gil/camera/fpv_x")
                fpv_y = s.get("/gil/camera/fpv_y")
                fpv_z = s.get("/gil/camera/fpv_z")
                fpv_pitch = s.get("/gil/camera/fpv_pitch")
                fpv_yaw = s.get("/gil/camera/fpv_yaw")
                fpv_roll = s.get("/gil/camera/fpv_roll")
                fpv_pos = Gf.Vec3d(
                    0.20 if fpv_x is None else float(fpv_x),
                    0.00 if fpv_y is None else float(fpv_y),
                    1.55 if fpv_z is None else float(fpv_z),
                )
                fpv_rot = Gf.Vec3f(
                    0.0 if fpv_pitch is None else float(fpv_pitch),
                    -90.0 if fpv_yaw is None else float(fpv_yaw),
                    0.0 if fpv_roll is None else float(fpv_roll),
                )
                xlate.Set(fpv_pos)
                rot.Set(fpv_rot)

            # Fixed third-person camera (static in world frame; best for seeing the robot move)
            tpv_path = "/World/ThirdPersonCamera"
            tpv_prim = stage.GetPrimAtPath(tpv_path)
            if not (tpv_prim and tpv_prim.IsValid() and tpv_prim.IsA(UsdGeom.Camera)):
                tpv = UsdGeom.Camera.Define(stage, tpv_path)
                tpv_prim = tpv.GetPrim()
            try:
                img = UsdGeom.Imageable(tpv_prim)
                img.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            except Exception:
                pass
            try:
                cam = UsdGeom.Camera(tpv_prim)
                cam.CreateFocalLengthAttr().Set(18.0)
                cam.CreateHorizontalApertureAttr().Set(20.955)
                cam.CreateVerticalApertureAttr().Set(11.778)
            except Exception:
                pass
            tpxf = UsdGeom.Xformable(tpv_prim)
            tp_ops = tpxf.GetOrderedXformOps()
            tp_xlate = None
            for op in tp_ops:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    tp_xlate = op
                    break
            if tp_xlate is None:
                tp_xlate = tpxf.AddTranslateOp()
            # Default fixed viewpoint; can be overridden by settings later.
            tp_x = s.get("/gil/camera/tpv_x")
            tp_y = s.get("/gil/camera/tpv_y")
            tp_z = s.get("/gil/camera/tpv_z")
            tp_xlate.Set(
                Gf.Vec3d(
                    -6.0 if tp_x is None else float(tp_x),
                    -7.0 if tp_y is None else float(tp_y),
                    3.0 if tp_z is None else float(tp_z),
                )
            )

            # Optional: create a small "robot camera suite" under the humanoid so the walker can capture
            # multiple views via /gil/camera/capture_all (it scans for UsdGeom.Camera under /World/Humanoid).
            #
            # This is especially useful for policy-based Unitree H1 (Isaac asset) where there may be no
            # pre-authored sensor cameras under the robot USD.
            suite_enabled = s.get("/gil/camera/ensure_robot_suite")
            suite_enabled = False if suite_enabled is None else bool(suite_enabled)
            if suite_enabled:
                try:
                    suite_parent = "/World/Humanoid"
                    pelvis = stage.GetPrimAtPath("/World/Humanoid/pelvis")
                    if pelvis and pelvis.IsValid():
                        suite_parent = "/World/Humanoid/pelvis"

                    suite_group = f"{suite_parent}/gil_cameras"
                    try:
                        UsdGeom.Xform.Define(stage, suite_group)
                    except Exception:
                        pass

                    created: list[str] = []

                    def _ensure_suite_cam(name: str, pos: tuple[float, float, float], rot_xyz: tuple[float, float, float]) -> None:
                        cam_path = f"{suite_group}/{name}"
                        prim = stage.GetPrimAtPath(cam_path)
                        if not (prim and prim.IsValid() and prim.IsA(UsdGeom.Camera)):
                            cam = UsdGeom.Camera.Define(stage, cam_path)
                            prim = cam.GetPrim()
                            created.append(cam_path)
                        try:
                            img = UsdGeom.Imageable(prim)
                            img.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                        except Exception:
                            pass
                        try:
                            cam = UsdGeom.Camera(prim)
                            # Reasonable defaults; capture resolution comes from /gil/camera/fpv_width/height.
                            cam.CreateFocalLengthAttr().Set(18.0)
                            cam.CreateHorizontalApertureAttr().Set(20.955)
                        except Exception:
                            pass

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
                        xlate.Set(Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2])))
                        rot.Set(Gf.Vec3f(float(rot_xyz[0]), float(rot_xyz[1]), float(rot_xyz[2])))

                    # Convention: match the synthetic FPVCamera default (yaw=-90) so forward faces robot +X.
                    _ensure_suite_cam("front_cam", (0.28, 0.00, 0.85), (0.0, -90.0, 0.0))
                    _ensure_suite_cam("world_cam", (-1.80, 0.00, 1.20), (0.0, -90.0, 0.0))
                    _ensure_suite_cam("left_wrist_cam", (0.35, -0.25, 0.65), (0.0, -90.0, 0.0))
                    _ensure_suite_cam("right_wrist_cam", (0.35, 0.25, 0.65), (0.0, -90.0, 0.0))

                    if created:
                        carb.log_info(f"[gil.unitree_h1_scene] ensured_robot_camera_suite={created}")
                except Exception as e:
                    carb.log_warn(f"[gil.unitree_h1_scene] Failed to ensure robot camera suite: {e!r}")

            # Follow camera behavior: update /OmniverseKit_Persp to follow the humanoid root pose.
            if follow_enabled:
                try:
                    if self._follow_task is None or self._follow_task.done():
                        self._follow_task = asyncio.ensure_future(self._follow_camera_loop())
                except Exception as e:
                    carb.log_warn(f"[gil.unitree_h1_scene] Failed to start follow camera loop: {e!r}")
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] Failed to ensure cameras: {e!r}")

    async def _follow_camera_loop(self) -> None:
        """
        Keeps the streamed viewport camera (/OmniverseKit_Persp) in a third-person follow position.
        """
        app = omni.kit.app.get_app()
        while True:
            try:
                stage = omni.usd.get_context().get_stage()
                if stage is None:
                    await app.next_update_async()
                    continue

                s = carb.settings.get_settings()
                enabled = s.get("/gil/camera/follow_enabled")
                enabled = True if enabled is None else bool(enabled)
                if not enabled:
                    await app.next_update_async()
                    continue

                humanoid = stage.GetPrimAtPath("/World/Humanoid")
                cam_prim = stage.GetPrimAtPath("/OmniverseKit_Persp")
                if not humanoid or not humanoid.IsValid() or not cam_prim or not cam_prim.IsValid():
                    await app.next_update_async()
                    continue

                from pxr import UsdGeom

                # Read robot world position (ignore orientation for now; we follow position only).
                hxf = UsdGeom.Xformable(humanoid)
                hmat = hxf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                hpos = hmat.ExtractTranslation()

                # Offsets (in world frame): behind (-Y) and up (+Z).
                ox = s.get("/gil/camera/follow_offset_x")
                oy = s.get("/gil/camera/follow_offset_y")
                oz = s.get("/gil/camera/follow_offset_z")
                offset = Gf.Vec3d(
                    -4.0 if ox is None else float(ox),
                    -5.0 if oy is None else float(oy),
                    3.0 if oz is None else float(oz),
                )
                eye = hpos + offset
                target = hpos + Gf.Vec3d(0.0, 0.0, 1.4)

                # Use helper when available; otherwise set translate only.
                try:
                    from isaacsim.core.utils.viewports import set_camera_view

                    set_camera_view(
                        eye=[float(eye[0]), float(eye[1]), float(eye[2])],
                        target=[float(target[0]), float(target[1]), float(target[2])],
                        camera_prim_path="/OmniverseKit_Persp",
                    )
                except Exception:
                    cxf = UsdGeom.Xformable(cam_prim)
                    xlate = cxf.AddTranslateOp()
                    xlate.Set(Gf.Vec3d(float(eye[0]), float(eye[1]), float(eye[2])))
            except Exception:
                # Never crash the sim for camera niceties.
                pass

            await app.next_update_async()

    def _set_default_camera(self) -> None:
        """
        Move the default perspective camera out of the robot body to avoid clipping.
        Works in streamed/headless sessions too.
        """
        try:
            # Preferred helper (if present)
            from isaacsim.core.utils.viewports import set_camera_view

            set_camera_view(
                eye=[-4.0, -5.0, 3.0],
                target=[0.0, 0.0, 1.5],
                camera_prim_path="/OmniverseKit_Persp",
            )
            carb.log_info("[gil.unitree_h1_scene] Camera moved to safe third-person view")
            return
        except Exception:
            pass

        # Fallback: set transform directly
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            from pxr import UsdGeom, Gf

            prim = stage.GetPrimAtPath("/OmniverseKit_Persp")
            if not prim or not prim.IsValid():
                return
            xf = UsdGeom.Xformable(prim)
            # Overwrite/append a translate op. (Good enough for our use.)
            xlate = xf.AddTranslateOp()
            xlate.Set(Gf.Vec3d(-6.0, -7.0, 3.0))
            carb.log_info("[gil.unitree_h1_scene] Camera translated to safe third-person view (fallback)")
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] Failed to move camera: {e!r}")

    def on_shutdown(self) -> None:
        carb.log_info("[gil.unitree_h1_scene] shutdown")
        try:
            if self._follow_task is not None:
                self._follow_task.cancel()
        except Exception:
            pass
        try:
            if self._attach_sensors_task is not None:
                self._attach_sensors_task.cancel()
        except Exception:
            pass


