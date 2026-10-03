import omni.ext
import carb
import omni.usd
import omni.client
import asyncio
import os
import json
import math
import omni.kit.app
from pxr import Gf
from pxr import Usd, UsdGeom
from pxr import UsdLux
from pxr import Vt

from .sensor_attachment import ensure_full_humanoid_sensors, AttachedSensorPrims


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        carb.log_info(f"[gil.unitree_h1_scene] startup ext_id={ext_id}")
        try:
            # Kit runs an asyncio loop internally; ensure_future schedules on it.
            self._task = asyncio.ensure_future(self._load_when_ready())
            self._follow_task = None
            self._cam_link_task = None
            self._cam_link_ops = None  # (translate_op, orient_op)
            self._cam_link_debug_until_s = None
            # True only when we synthesized `/World/Humanoid/camera_link` via sensors_config parent creation.
            self._synthetic_camera_link = False
            self._attached_sensors: AttachedSensorPrims | None = None
            self._attach_sensors_task: asyncio.Task | None = None
        except Exception as e:
            carb.log_error(f"[gil.unitree_h1_scene] Failed to start async loader: {e!r}")
            self._task = None
            self._follow_task = None
            self._cam_link_task = None
            self._cam_link_ops = None
            self._cam_link_debug_until_s = None
            self._synthetic_camera_link = False
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
            ctx = omni.usd.get_context()
            stage_requested = False
            # Wait up to ~120s wall-clock time; some experiences bring up the stage late.
            deadline_s = asyncio.get_running_loop().time() + 120.0
            while asyncio.get_running_loop().time() < deadline_s:
                stage = ctx.get_stage()
                if stage is not None:
                    break
                if not stage_requested:
                    # Some experiences delay creating the stage; request one so our references can load.
                    try:
                        ctx.new_stage()
                        stage_requested = True
                    except Exception:
                        # Keep waiting; we'll try again after a few updates.
                        pass
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

            # Default environment, but allow override for demos:
            # - absolute file path
            # - assets-root-relative path like "/Isaac/Environments/..."
            # - preset name via /gil/env/preset (best-effort)
            env_default = assets_root + "/Isaac/Environments/Grid/default_environment.usd"
            env_usd = env_default
            settings = carb.settings.get_settings()
            try:
                env_override = str(settings.get("/gil/env/usd") or "").strip()
            except Exception:
                env_override = ""
            try:
                env_preset = str(settings.get("/gil/env/preset") or "").strip().lower()
            except Exception:
                env_preset = ""

            def _resolve_env(rel_or_abs: str) -> str:
                if os.path.isabs(rel_or_abs) and os.path.exists(rel_or_abs):
                    return rel_or_abs
                rel = rel_or_abs if rel_or_abs.startswith("/") else ("/" + rel_or_abs)
                return str(assets_root).rstrip("/") + rel

            def _exists(url: str) -> bool:
                try:
                    res, _ = omni.client.stat(url)
                    if res == omni.client.Result.OK:
                        return True
                except Exception:
                    pass
                # Isaac 5.1 S3: omni.client.stat often misses Unitree/G1_23dof even when HTTP 200.
                if str(url).startswith("http://") or str(url).startswith("https://"):
                    try:
                        import urllib.request

                        req = urllib.request.Request(url, method="HEAD")
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            code = int(getattr(resp, "status", 0) or 0)
                            length = 0
                            try:
                                length = int(resp.headers.get("Content-Length") or 0)
                            except Exception:
                                length = 0
                            # Tiny objects are often stubs/listings, not a robot USD.
                            return 200 <= code < 300 and length >= 100_000
                    except Exception:
                        return False
                return False

            # Apply override/preset with existence checks (avoid hard failing on missing assets).
            if env_override:
                env_try = _resolve_env(env_override)
                # If env_try points to a non-USD mesh file, auto-convert to USD and cache near the source.
                env_conv = None
                try:
                    env_conv = await self._maybe_convert_asset_to_usd(env_try)
                except Exception as e:
                    carb.log_warn(f"[gil.unitree_h1_scene] env_override convert failed: {e!r}")
                    env_conv = None
                if env_conv:
                    env_usd = env_conv
                    carb.log_warn(f"[gil.unitree_h1_scene] env_override converted: {env_try} -> {env_usd}")
                elif _exists(env_try):
                    env_usd = env_try
                    carb.log_warn(f"[gil.unitree_h1_scene] env_override={env_override} resolved={env_usd}")
                else:
                    carb.log_warn(f"[gil.unitree_h1_scene] env_override not found: {env_override} resolved={env_try}; using default grid env")
            elif env_preset:
                preset_map = {
                    "grid": ["/Isaac/Environments/Grid/default_environment.usd"],
                    # These vary across content packs; try a few common candidates.
                    "simple_room": [
                        "/Isaac/Environments/Simple_Room/simple_room.usd",
                        "/Isaac/Environments/SimpleRoom/simple_room.usd",
                        "/Isaac/Environments/Simple_Room/room.usd",
                    ],
                    "warehouse": [
                        "/Isaac/Environments/Warehouse/warehouse.usd",
                        "/Isaac/Environments/Simple_Warehouse/warehouse.usd",
                        "/Isaac/Environments/SimpleWarehouse/warehouse.usd",
                    ],
                    "office": [
                        "/Isaac/Environments/Office/office.usd",
                        "/Isaac/Environments/Simple_Office/office.usd",
                        "/Isaac/Environments/SimpleOffice/office.usd",
                    ],
                    "hospital": [
                        "/Isaac/Environments/Hospital/hospital.usd",
                        "/Isaac/Environments/Simple_Hospital/hospital.usd",
                    ],
                }
                candidates = preset_map.get(env_preset, [])
                for rel in candidates:
                    env_try = _resolve_env(rel)
                    if _exists(env_try):
                        env_usd = env_try
                        break
                if env_usd != env_default:
                    carb.log_warn(f"[gil.unitree_h1_scene] env_preset={env_preset} resolved={env_usd}")
                else:
                    carb.log_warn(f"[gil.unitree_h1_scene] env_preset={env_preset} not found in assets root; using default grid env")

            # If selected env is a local mesh file, auto-convert it to USD as well.
            try:
                env_conv = await self._maybe_convert_asset_to_usd(env_usd)
                if env_conv:
                    env_usd = env_conv
            except Exception:
                pass

            # Allow overriding the robot selection (relative to assets_root).
            # - Kit setting: /gil/humanoid/usd_rel
            # - Env var: GIL_HUMANOID_USD_REL
            desired_rel = settings.get("/gil/humanoid/usd_rel") or os.environ.get("GIL_HUMANOID_USD_REL", "")
            desired_rel = str(desired_rel).strip()

            # Optional: prefer local Unitree H1-2 asset pack shipped in this repo (IsaacLab third-party).
            # This makes "try H1-2 first" trivial while keeping default behavior unchanged unless enabled.
            def _as_bool(v, default: bool = False) -> bool:
                if v is None:
                    return default
                if isinstance(v, bool):
                    return bool(v)
                return str(v).strip().lower() in ("1", "true", "yes", "on")

            prefer_g1 = _as_bool(settings.get("/gil/humanoid/prefer_g1"), False)
            try:
                variant_now = str(settings.get("/gil/humanoid/variant") or "").lower().strip()
            except Exception:
                variant_now = ""
            if variant_now in ("g1", "unitree_g1"):
                prefer_g1 = True

            prefer_h12 = settings.get("/gil/humanoid/prefer_h1_2")
            prefer_h12 = _as_bool(prefer_h12, str(os.environ.get("GIL_PREFER_H1_2", "")).strip().lower() in ("1", "true", "yes", "on"))
            local_h12_usd = ""
            if prefer_h12 and (not desired_rel) and (not prefer_g1):
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

            prefer_g1 = prefer_g1  # already set above from variant / setting

            g1_first = [
                "/Isaac/Robots/Unitree/G1_23dof/g1.usd",
                "/Isaac/Robots/Unitree/G1_23dof/g1_minimal.usd",
                "/Isaac/Robots/Unitree/G1/g1.usd",
                "/Isaac/Robots/Unitree/G1/g1_29dof.usd",
                "/Isaac/Robots/Unitree/G1/g1_minimal.usd",
                "/Isaac/Robots/Unitree/G1/g1_robot.usd",
                "/Isaac/Robots/Unitree/G1/unitree_g1.usd",
            ]

            prefer_h1 = _as_bool(settings.get("/gil/humanoid/prefer_h1_policy"), False)

            candidates_rel = [
                "/Isaac/Robots/Unitree/H1_2/h1_2.usd",
                "/Isaac/Robots/Unitree/H1_2/h1_2_robot.usd",
                "/Isaac/Robots/Unitree/H1-2/h1-2.usd",
                "/Isaac/Robots/Unitree/G1_23dof/g1.usd",
                "/Isaac/Robots/Unitree/G1_23dof/g1_minimal.usd",
                "/Isaac/Robots/Unitree/G1/g1.usd",
                "/Isaac/Robots/Unitree/G1/g1_29dof.usd",
                "/Isaac/Robots/Unitree/G1/g1_minimal.usd",
                "/Isaac/Robots/Unitree/G1/g1_robot.usd",
                "/Isaac/Robots/Unitree/G1/unitree_g1.usd",
                "/Isaac/Robots/NVIDIA/Humanoid/humanoid.usd",
                "/Isaac/Robots/IsaacSim/Humanoid/humanoid.usd",
                "/Isaac/Robots/Unitree/H1/h1.usd",
            ]
            if prefer_h1 and (not prefer_g1):
                candidates_rel = ["/Isaac/Robots/Unitree/H1/h1.usd"] + [c for c in candidates_rel if c != "/Isaac/Robots/Unitree/H1/h1.usd"]
            if prefer_g1:
                candidates_rel = list(g1_first)

            if desired_rel and (not prefer_g1 or "g1" in str(desired_rel).lower()):
                candidates_rel = [desired_rel] + [c for c in candidates_rel if c != desired_rel]
            elif local_h12_usd and (not prefer_g1):
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
                if prefer_g1:
                    # omni.client.stat (and even HTTP HEAD from Kit) often miss G1_23dof on 5.1 S3.
                    # The asset exists: .../Isaac/Robots/Unitree/G1_23dof/g1.usd (~22MB).
                    chosen_url = str(assets_root).rstrip("/") + "/Isaac/Robots/Unitree/G1_23dof/g1.usd"
                    carb.log_warn(
                        f"[gil.unitree_h1_scene] G1 stat missed under {assets_root} tried={candidates_rel}; "
                        f"forcing {chosen_url}"
                    )
                else:
                    carb.log_warn(
                        "[gil.unitree_h1_scene] No humanoid USD candidates found in assets root; falling back to H1 path anyway"
                    )
                    chosen_url = assets_root + "/Isaac/Robots/Unitree/H1/h1.usd"

            robot_usd = chosen_url
            carb.log_warn(f"[gil.unitree_h1_scene] selected_robot_usd={robot_usd} variant={variant_now} prefer_g1={prefer_g1}")

            # Load environment + robot
            try:
                stage_utils.add_reference_to_stage(env_usd, prim_path="/World/Env")
                stage_utils.add_reference_to_stage(robot_usd, prim_path="/World/Humanoid")
            except Exception as e:
                carb.log_error(f"[gil.unitree_h1_scene] Failed to add references: {e!r}")
                return

            # Ensure at least one light exists so RTX viewports don't appear black.
            try:
                dome_path = "/World/GIL_DomeLight"
                if not (stage.GetPrimAtPath(dome_path) and stage.GetPrimAtPath(dome_path).IsValid()):
                    dome = UsdLux.DomeLight.Define(stage, dome_path)
                    dome.CreateIntensityAttr(1500.0)
                    dome.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Failed to ensure dome light: {e!r}")

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
                # Alias imports to avoid shadowing the module-level `UsdLux` name earlier in this function.
                from pxr import UsdLux as _UsdLux, UsdGeom as _UsdGeom

                _UsdGeom.Scope.Define(stage, "/World/Lights")

                dome = _UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
                dome.CreateIntensityAttr(800.0)

                sun = _UsdLux.DistantLight.Define(stage, "/World/Lights/SunLight")
                sun.CreateIntensityAttr(3000.0)
                # Idempotent rotate op (avoid "already exists" errors on reloads)
                xf = _UsdGeom.Xformable(sun.GetPrim())
                rot = None
                for op in xf.GetOrderedXformOps():
                    if op.GetOpType() == _UsdGeom.XformOp.TypeRotateXYZ:
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
                    # Spawn at the maze entrance (bottom-left cell center) so the humanoid starts inside the maze corridor.
                    # Keep this in sync with `gil.maze_env` placement settings.
                    try:
                        s = carb.settings.get_settings()
                        origin_x = float(s.get("/gil/maze/origin_x") or -4.0)
                        origin_y = float(s.get("/gil/maze/origin_y") or -4.0)
                        cell = float(s.get("/gil/maze/cell_size") or 1.4)
                        # Default to an interior cell so the humanoid doesn't start out scraping boundary walls.
                        scx = int(s.get("/gil/maze/spawn_cell_x") or 1)
                        scy = int(s.get("/gil/maze/spawn_cell_y") or 1)
                        # Clamp to grid if available.
                        w = int(s.get("/gil/maze/width") or 9)
                        h = int(s.get("/gil/maze/height") or 9)
                        scx = max(0, min(w - 1, int(scx)))
                        scy = max(0, min(h - 1, int(scy)))
                        spawn_x = origin_x + (scx + 0.5) * cell
                        spawn_y = origin_y + (scy + 0.5) * cell
                    except Exception:
                        spawn_x, spawn_y, cell = -1.9, -1.9, 1.4
                    try:
                        spawn_z = s.get("/gil/humanoid/spawn_z")
                        spawn_z = 1.05 if spawn_z is None else float(spawn_z)
                    except Exception:
                        spawn_z = 1.05
                    spawn_z = max(1.00, min(1.20, float(spawn_z)))
                    xlate.Set(Gf.Vec3d(float(spawn_x), float(spawn_y), spawn_z))

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

            # Optional: load a reconstructed/converted environment artifact for demos (e.g. pointcloud).
            try:
                self._maybe_load_converted_environment(stage)
            except Exception as e:
                carb.log_warn(f"[gil.unitree_h1_scene] Converted env load failed: {e!r}")

            carb.log_info("[gil.unitree_h1_scene] Humanoid scene loaded. Press Play in Isaac Sim to start physics.")
            self._set_default_camera()
        except Exception as e:
            carb.log_error(f"[gil.unitree_h1_scene] Unhandled exception in async loader: {e!r}")

    async def _maybe_convert_asset_to_usd(self, path_or_url: str) -> str | None:
        """
        If `path_or_url` is a local mesh file (.ply/.obj/.stl/.glb/.gltf/.fbx),
        convert it to USD (cached) and return the USD path. Otherwise return None.
        """
        p = str(path_or_url or "").strip()
        if not p:
            return None
        # Only convert local files
        if p.startswith("http://") or p.startswith("https://") or p.startswith("omniverse://"):
            return None
        if not os.path.isabs(p) or (not os.path.exists(p)):
            return None
        ext = os.path.splitext(p)[1].lower().strip(".")
        if ext in ("usd", "usda", "usdc"):
            return None
        if ext not in ("ply", "obj", "stl", "fbx", "glb", "gltf"):
            return None
        out_usd = os.path.join(os.path.dirname(p), "usd_cache", os.path.splitext(os.path.basename(p))[0] + ".usd")
        os.makedirs(os.path.dirname(out_usd), exist_ok=True)
        if os.path.exists(out_usd):
            return out_usd

        # Enable converter extension and run conversion task
        try:
            import omni.kit.app
            import omni.kit.asset_converter as ac
        except Exception:
            # Enable extension then import
            try:
                import omni.kit.app

                app = omni.kit.app.get_app()
                em = app.get_extension_manager()
                em.set_extension_enabled_immediate("omni.kit.asset_converter", True)
            except Exception:
                pass
            import omni.kit.asset_converter as ac  # type: ignore

        ctx = ac.AssetConverterContext()
        # bake scale into geometry (important for Isaac)
        ctx.baking_scales = True
        ctx.use_double_precision_to_usd_transform_op = True
        ctx.embed_textures = True
        ctx.ignore_animations = True
        ctx.ignore_cameras = True
        ctx.ignore_lights = True
        # The converter sometimes outputs cm; baking scales helps.
        ctx.use_meter_as_world_unit = True
        inst = ac.get_instance()
        task = inst.create_converter_task(p, out_usd, None, ctx)
        ok = await task.wait_until_finished()
        if not ok:
            raise RuntimeError(f"asset_converter failed: {task.get_error_message()}")
        return out_usd

    def _maybe_load_converted_environment(self, stage: Usd.Stage | None) -> None:
        """
        Load demo-friendly reconstructed artifacts into the USD stage.

        Supported:
        - Pointcloud `.npz` with arrays:
            - points_xyz: (N,3) float32/float64
            - colors_rgb: (N,3) uint8 or float in [0,1]

        Settings:
          /gil/env_converted/enabled (bool)
          /gil/env_converted/pointcloud_npz (str path)
          /gil/env_converted/prim_path (str, default "/World/EnvConverted")
          /gil/env_converted/point_size (float, default 0.03)
          /gil/env_converted/auto_align_to_env (bool, default true)
          /gil/env_converted/rotate_x_deg (float, default 0.0)
          /gil/env_converted/rotate_y_deg (float, default 0.0)
          /gil/env_converted/rotate_z_deg (float, default 0.0)
        """
        if stage is None:
            return
        try:
            s = carb.settings.get_settings()
            enabled = s.get("/gil/env_converted/enabled")
            enabled = False if enabled is None else bool(enabled)
            if not enabled:
                return
            npz_path = str(s.get("/gil/env_converted/pointcloud_npz") or "").strip()
            prim_path = str(s.get("/gil/env_converted/prim_path") or "/World/EnvConverted").strip() or "/World/EnvConverted"
            point_size = float(s.get("/gil/env_converted/point_size") or 0.03)
            auto_align = s.get("/gil/env_converted/auto_align_to_env")
            auto_align = True if auto_align is None else bool(auto_align)
            rx = float(s.get("/gil/env_converted/rotate_x_deg") or 0.0)
            ry = float(s.get("/gil/env_converted/rotate_y_deg") or 0.0)
            rz = float(s.get("/gil/env_converted/rotate_z_deg") or 0.0)
        except Exception:
            return

        if not npz_path:
            carb.log_warn("[gil.unitree_h1_scene] /gil/env_converted/enabled=true but no /gil/env_converted/pointcloud_npz set")
            return
        if not os.path.exists(npz_path):
            carb.log_warn(f"[gil.unitree_h1_scene] converted pointcloud not found: {npz_path}")
            return

        try:
            import numpy as np
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] numpy not available for pointcloud load: {e!r}")
            return

        data = np.load(npz_path)
        pts = np.asarray(data.get("points_xyz"))
        cols = np.asarray(data.get("colors_rgb")) if "colors_rgb" in data else None
        if pts.ndim != 2 or pts.shape[1] != 3 or pts.size == 0:
            carb.log_warn(f"[gil.unitree_h1_scene] invalid points_xyz in {npz_path}: shape={getattr(pts,'shape',None)!r}")
            return
        if cols is None or cols.ndim != 2 or cols.shape[1] != 3:
            cols = np.ones((pts.shape[0], 3), dtype=np.float32)

        # Normalize colors to float [0,1]
        if cols.dtype == np.uint8:
            cols_f = (cols.astype(np.float32) / 255.0).clip(0.0, 1.0)
        else:
            cols_f = cols.astype(np.float32)
            mx = float(np.nanmax(cols_f)) if cols_f.size else 1.0
            if mx > 1.5:
                cols_f = (cols_f / 255.0).clip(0.0, 1.0)
            else:
                cols_f = cols_f.clip(0.0, 1.0)

        # Create/replace prims
        try:
            prim0 = stage.GetPrimAtPath(prim_path)
            if prim0 and prim0.IsValid():
                stage.RemovePrim(prim_path)
        except Exception:
            pass
        xform = UsdGeom.Xform.Define(stage, prim_path)
        xf = UsdGeom.Xformable(xform.GetPrim())

        # If the main environment exists, auto-align the pointcloud to it by matching AABBs under
        # all axis-permutation rotations (24 right-handed orthonormal bases). This avoids guessing
        # asset-converter axis behavior and fixes 90° rotated / upside-down / underground overlays.
        applied_auto = False
        try:
            env_prim = stage.GetPrimAtPath("/World/Env")
            has_manual_rot = (abs(rx) > 1e-4) or (abs(ry) > 1e-4) or (abs(rz) > 1e-4)
            if auto_align and env_prim and env_prim.IsValid() and (not has_manual_rot):
                # Compute env AABB in world space
                bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"], useExtentsHint=True)
                b = bbox_cache.ComputeWorldBound(env_prim)
                r = b.ComputeAlignedRange()
                env_min = np.asarray([float(r.GetMin()[0]), float(r.GetMin()[1]), float(r.GetMin()[2])], dtype=np.float64)
                env_max = np.asarray([float(r.GetMax()[0]), float(r.GetMax()[1]), float(r.GetMax()[2])], dtype=np.float64)

                pts64 = pts.astype(np.float64, copy=False)
                pc_min0 = pts64.min(axis=0)
                pc_max0 = pts64.max(axis=0)

                # Generate 24 right-handed axis-permutation rotation matrices.
                axes = np.eye(3, dtype=np.float64)
                rots: list[np.ndarray] = []
                import itertools

                for perm in itertools.permutations([0, 1, 2], 3):
                    P = axes[list(perm), :]
                    for signs in itertools.product([-1.0, 1.0], repeat=3):
                        S = np.diag(signs)
                        Rm = S @ P
                        if np.linalg.det(Rm) < 0.0:
                            continue
                        rots.append(Rm)

                best_T = None
                best_err = None
                env_center = (env_min + env_max) * 0.5
                env_extent = (env_max - env_min)

                for Rm in rots:
                    # Rotate points, then evaluate how well extents match.
                    pr = pts64 @ Rm.T
                    pc_min = pr.min(axis=0)
                    pc_max = pr.max(axis=0)
                    pc_center = (pc_min + pc_max) * 0.5
                    pc_extent = (pc_max - pc_min)

                    # Translation strategy:
                    # - XY: match centers (stable, avoids "in the ground" from noisy mins)
                    # - Z: match floors (minZ) so it sits on ground
                    t = np.asarray(
                        [
                            float(env_center[0] - pc_center[0]),
                            float(env_center[1] - pc_center[1]),
                            float(env_min[2] - pc_min[2]),
                        ],
                        dtype=np.float64,
                    )

                    # Error combines extent mismatch and ceiling mismatch (after floor match)
                    extent_err = float(np.linalg.norm(pc_extent - env_extent))
                    ceil_err = float(abs((pc_max[2] + t[2]) - env_max[2]))
                    err = extent_err + 0.25 * ceil_err
                    if (best_err is None) or (err < best_err):
                        best_err = err
                        best_T = (Rm, t)

                if best_T is not None and best_err is not None:
                    Rm, t = best_T
                    M = Gf.Matrix4d(
                        Rm[0, 0], Rm[0, 1], Rm[0, 2], 0.0,
                        Rm[1, 0], Rm[1, 1], Rm[1, 2], 0.0,
                        Rm[2, 0], Rm[2, 1], Rm[2, 2], 0.0,
                        float(t[0]), float(t[1]), float(t[2]), 1.0,
                    )
                    op = xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble)
                    op.Set(M)
                    applied_auto = True
                    carb.log_warn(f"[gil.unitree_h1_scene] auto_aligned pointcloud to /World/Env (aabb_err={best_err:.4f})")
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] auto_align_to_env failed: {e!r}")

        # Manual Euler rotation (fallback / explicit override)
        if not applied_auto:
            try:
                if abs(rx) > 1e-4 or abs(ry) > 1e-4 or abs(rz) > 1e-4:
                    op = xf.AddRotateXYZOp(UsdGeom.XformOp.PrecisionFloat)
                    op.Set(Gf.Vec3f(float(rx), float(ry), float(rz)))
            except Exception:
                pass
        pts_path = prim_path.rstrip("/") + "/PointCloud"
        pprim = UsdGeom.Points.Define(stage, pts_path)

        vt_pts = Vt.Vec3fArray([Gf.Vec3f(float(x), float(y), float(z)) for x, y, z in pts.tolist()])
        pprim.CreatePointsAttr(vt_pts)
        pprim.CreateWidthsAttr(Vt.FloatArray([float(point_size)] * int(pts.shape[0])))
        col_arr = Vt.Vec3fArray([Gf.Vec3f(float(r), float(g), float(b)) for r, g, b in cols_f.tolist()])
        pv = pprim.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)
        pv.Set(col_arr)

        carb.log_warn(f"[gil.unitree_h1_scene] loaded converted pointcloud: {npz_path} -> {pts_path} (N={int(pts.shape[0])})")

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
            # Default to a known-good "original" Unitree sensor profile shipped with this repo.
            # This keeps FPV camera placement stable across sessions even when the robot USD lacks sensor prims.
            try:
                variant = str(s.get("/gil/humanoid/variant") or "h1").lower().strip()
            except Exception:
                variant = "h1"
            try:
                from pathlib import Path

                here = Path(__file__).resolve()
                chosen = None
                for p in here.parents:
                    cand = p / "robot_simulator" / "config" / "robots"
                    if cand.is_dir():
                        if variant in ("h1_2", "h1-2"):
                            f = cand / "unitree_h1_2_sensors.json"
                        elif variant in ("g1", "unitree_g1"):
                            f = cand / "unitree_g1_sensors.json"
                        else:
                            f = cand / "unitree_h1_sensors.json"
                        if f.is_file():
                            chosen = str(f)
                            break
                if chosen:
                    cfg_path = chosen
                    try:
                        s.set("/gil/robot/sensors_config", cfg_path)
                    except Exception:
                        pass
                    carb.log_info(f"[gil.unitree_h1_scene] defaulted sensors_config={cfg_path}")
            except Exception:
                pass
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

        # Default FPV camera prim selection:
        # - H1-2: use the real sensor camera link prim_path
        # - H1: always /World/Humanoid/FPVCamera (world-driven). H1-2 link offsets put the camera inside the H1 mesh.
        try:
            variant = str(s.get("/gil/humanoid/variant") or "h1").lower().strip()
        except Exception:
            variant = "h1"
        is_h12 = variant in ("h1_2", "h1-2", "h12") or variant.startswith("h1_2")
        fpv_prim_path = fpv.get("prim_path")
        if is_h12 and isinstance(fpv_prim_path, str) and fpv_prim_path.strip():
            _set_if("/gil/camera/fpv_prim_path", fpv_prim_path.strip())
        elif variant in ("g1", "unitree_g1"):
            # Let USD discovery pick a real G1 camera; do not force synthetic FPVCamera.
            pass
        else:
            _set_if("/gil/camera/fpv_prim_path", "/World/Humanoid/FPVCamera")

        # FPV resolution (used by capture)
        res = fpv.get("resolution_px") or fpv.get("resolution") or fpv.get("res_px")
        if isinstance(res, list) and len(res) == 2:
            _set_if("/gil/camera/fpv_width", int(res[0]))
            _set_if("/gil/camera/fpv_height", int(res[1]))

        # Legacy H1 profile supports extrinsics as euler+pos directly under the fpv entry.
        # Map them into the synthetic FPVCamera settings used by `_ensure_cameras()`.
        pos = fpv.get("pos_m") or fpv.get("pos")
        rot = fpv.get("rot_euler_deg_xyz") or fpv.get("rot_euler_deg") or fpv.get("rot")
        if isinstance(pos, list) and len(pos) == 3:
            _set_if("/gil/camera/fpv_x", float(pos[0]))
            _set_if("/gil/camera/fpv_y", float(pos[1]))
            _set_if("/gil/camera/fpv_z", float(pos[2]))
        if isinstance(rot, list) and len(rot) == 3:
            # Our synthetic camera uses RotateXYZ ops with (pitch, yaw, roll) ordering in degrees.
            _set_if("/gil/camera/fpv_pitch", float(rot[0]))
            _set_if("/gil/camera/fpv_yaw", float(rot[1]))
            _set_if("/gil/camera/fpv_roll", float(rot[2]))

        # Legacy H1 intrinsics in mm.
        fl = fpv.get("focal_length_mm") or fpv.get("focal_length")
        hap = fpv.get("horizontal_aperture_mm") or fpv.get("horizontal_aperture")
        vap = fpv.get("vertical_aperture_mm") or fpv.get("vertical_aperture")
        if fl is not None:
            _set_if("/gil/camera/fpv_focal_length_mm", float(fl))
            _set_if("/gil/camera/override_intrinsics", True)
        if hap is not None:
            _set_if("/gil/camera/fpv_horizontal_aperture_mm", float(hap))
            _set_if("/gil/camera/override_intrinsics", True)
        if vap is not None:
            _set_if("/gil/camera/fpv_vertical_aperture_mm", float(vap))
            _set_if("/gil/camera/override_intrinsics", True)

        # Wide/debug resolution (used for capture, not a physical sensor)
        wres = wide.get("resolution_px")
        if isinstance(wres, list) and len(wres) == 2:
            _set_if("/gil/camera/wide_width", int(wres[0]))
            _set_if("/gil/camera/wide_height", int(wres[1]))

        chase = (cams.get("chase") or {})
        cpos = chase.get("pos_m")
        if isinstance(cpos, list) and len(cpos) == 3:
            _set_if("/gil/camera/chase_x", float(cpos[0]))
            _set_if("/gil/camera/chase_y", float(cpos[1]))
            _set_if("/gil/camera/chase_z", float(cpos[2]))
        cprim = chase.get("prim_path")
        if isinstance(cprim, str) and cprim.strip() and not is_h12:
            _set_if("/gil/camera/tpv_prim_path", cprim.strip())
            _set_if("/gil/camera/chase_prim_path", cprim.strip())

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
        try:
            variant = str(s.get("/gil/humanoid/variant") or "h1").lower().strip()
        except Exception:
            variant = "h1"
        is_h12 = variant in ("h1_2", "h1-2", "h12") or variant.startswith("h1_2")
        is_g1 = variant in ("g1", "unitree_g1")
        if (not is_h12) and (not is_g1):
            # Do not parent H1-2 cameras onto H1 torso/elbow frames (inside mesh / world origin).
            try:
                humanoid = stage.GetPrimAtPath("/World/Humanoid")
                if humanoid and humanoid.IsValid():
                    for p in list(Usd.PrimRange(humanoid)):
                        nm = p.GetName() or ""
                        path = p.GetPath().pathString
                        if nm in ("front_cam", "world_cam", "left_wrist_cam", "right_wrist_cam") and "/gil_cameras/" not in path:
                            try:
                                stage.RemovePrim(p.GetPath())
                            except Exception:
                                pass
            except Exception:
                pass
            return
        if is_g1:
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
        created_parent_xforms: set[str] = set()

        def _find_humanoid_link(name_tokens: list[str]) -> str | None:
            root = stage.GetPrimAtPath("/World/Humanoid")
            if not (root and root.IsValid()):
                return None
            toks = [t.lower() for t in name_tokens if t]
            exact = []
            for t in toks:
                exact.extend(
                    [
                        f"/World/Humanoid/{t}",
                        f"/World/Humanoid/Joints/{t}",
                    ]
                )
            for cand in exact:
                p = stage.GetPrimAtPath(cand)
                if p and p.IsValid():
                    return cand
            for p in Usd.PrimRange(root):
                try:
                    nm = (p.GetName() or "").lower()
                except Exception:
                    continue
                if nm in toks:
                    return p.GetPath().pathString
            for p in Usd.PrimRange(root):
                try:
                    nm = (p.GetName() or "").lower()
                    path_l = p.GetPath().pathString.lower()
                except Exception:
                    continue
                if any(t in nm or t in path_l for t in toks):
                    return p.GetPath().pathString
            return None

        def _remap_camera_prim_path(prim_path: str) -> str:
            """Re-parent H1-2 camera paths onto links that exist on the loaded H1 USD."""
            parent_path = os.path.dirname(prim_path.replace("\\", "/"))
            parent_prim = stage.GetPrimAtPath(parent_path)
            if parent_prim and parent_prim.IsValid():
                return prim_path
            leaf = os.path.basename(prim_path.replace("\\", "/"))
            parent_name = os.path.basename(parent_path).lower()
            if "left" in parent_name and any(t in parent_name for t in ("hand", "wrist", "elbow", "camera")):
                hints = ["left_wrist_yaw_link", "left_wrist_roll_link", "left_elbow_link", "left_elbow_pitch_link"]
            elif "right" in parent_name and any(t in parent_name for t in ("hand", "wrist", "elbow", "camera")):
                hints = ["right_wrist_yaw_link", "right_wrist_roll_link", "right_elbow_link", "right_elbow_pitch_link"]
            else:
                hints = ["torso", "torso_link", "pelvis"]
            found = _find_humanoid_link(hints)
            if not found:
                return prim_path
            return f"{found}/{leaf}"

        def _ensure_camera(cam_cfg: dict) -> None:
            if not isinstance(cam_cfg, dict):
                return
            prim_path = cam_cfg.get("prim_path")
            if not isinstance(prim_path, str) or not prim_path.strip():
                return
            prim_path = _remap_camera_prim_path(prim_path.strip())
            cam_cfg["prim_path"] = prim_path

            # Make sure the parent link exists; if not, skip (we must not create fake links).
            parent_path = os.path.dirname(prim_path.replace("\\", "/"))
            parent_prim = stage.GetPrimAtPath(parent_path)
            if not (parent_prim and parent_prim.IsValid()):
                # Optional: create missing parent Xform chain so we can reproduce Unitree's "original" prim paths
                # even when using an Isaac-shipped robot USD that doesn't include the sensor link hierarchy.
                allow_create = s.get("/gil/camera/allow_create_camera_parents")
                allow_create = False if allow_create is None else bool(allow_create)
                if allow_create and prim_path.startswith("/World/Humanoid/"):
                    try:
                        # Create Xform prims for each missing parent in the chain.
                        parts = parent_path.split("/")
                        cur = ""
                        for part in parts:
                            if part == "":
                                continue
                            cur = cur + "/" + part
                            if cur in ("/World", "/World/Humanoid"):
                                continue
                            p = stage.GetPrimAtPath(cur)
                            if p and p.IsValid():
                                continue
                            try:
                                xf = UsdGeom.Xform.Define(stage, cur)
                                try:
                                    xf.GetPrim().SetCustomDataByKey("gil:synthetic_camera_parent", True)
                                    created_parent_xforms.add(cur)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        parent_prim = stage.GetPrimAtPath(parent_path)
                    except Exception:
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

        # If we had to synthesize the sensor parent hierarchy (camera_link), the sensors_config FPV prim path
        # typically points to `/World/Humanoid/camera_link/front_cam`, which is not guaranteed to move with physics.
        # In that case, force FPV back to our synthetic camera path; `_ensure_cameras()` will attach it under pelvis.
        if "/World/Humanoid/camera_link" in created_parent_xforms:
            try:
                s.set("/gil/camera/fpv_prim_path", "/World/Humanoid/FPVCamera")
                carb.log_info("[gil.unitree_h1_scene] camera_link synthesized: overriding fpv_prim_path=/World/Humanoid/FPVCamera")
            except Exception:
                pass

        # Prefer front_cam as FPV if present *only* when it comes from a real sensor rig in the robot USD.
        # If we had to synthesize `camera_link` parents, those cameras are prone to "stuck at spawn" unless we
        # also run an attach loop. In that case, fall back to the pelvis-attached synthetic FPV camera path,
        # which uses simple USD parenting (Isaac Sim recommended).
        if (
            "front_cam" in cams
            and isinstance(cams["front_cam"], dict)
            and isinstance(cams["front_cam"].get("prim_path"), str)
        ):
            # Only use camera_link/front_cam as FPV if camera_link was not synthesized.
            # When we synthesize camera_link, prefer the pelvis-attached synthetic FPV camera created later.
            use_front = ("/World/Humanoid/camera_link" not in created_parent_xforms)
            try:
                carb.log_info(
                    f"[gil.unitree_h1_scene] fpv_select front_cam use_front={bool(use_front)} "
                    f"camera_link_synth={bool('/World/Humanoid/camera_link' in created_parent_xforms)}"
                )
            except Exception:
                pass
            if bool(use_front):
                s.set("/gil/camera/fpv_prim_path", str(cams["front_cam"]["prim_path"]))

        if created:
            carb.log_info(f"[gil.unitree_h1_scene] ensured_cameras={created}")

        # If we synthesized a camera_link hierarchy, keep it attached to the moving body link so the cameras
        # actually follow the robot (instead of staying stuck near spawn).
        try:
            if "/World/Humanoid/camera_link" in created_parent_xforms:
                attach_enabled = s.get("/gil/camera/attach_synthetic_camera_link")
                attach_enabled = True if attach_enabled is None else bool(attach_enabled)
                if attach_enabled:
                    try:
                        self._synthetic_camera_link = True
                    except Exception:
                        pass
                    if self._cam_link_task is None or self._cam_link_task.done():
                        self._cam_link_task = asyncio.ensure_future(self._attach_synthetic_camera_link_loop())
                        carb.log_info("[gil.unitree_h1_scene] attaching synthetic /World/Humanoid/camera_link to moving body link")
                else:
                    # If we synthesized camera_link, we *must* attach it; otherwise robot cameras appear stuck at spawn.
                    # Force-enable and start the loop.
                    try:
                        s.set("/gil/camera/attach_synthetic_camera_link", True)
                    except Exception:
                        pass
                    try:
                        self._synthetic_camera_link = True
                    except Exception:
                        pass
                    if self._cam_link_task is None or self._cam_link_task.done():
                        self._cam_link_task = asyncio.ensure_future(self._attach_synthetic_camera_link_loop())
                        carb.log_info("[gil.unitree_h1_scene] attaching synthetic /World/Humanoid/camera_link to moving body link (forced)")
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] Failed to start synthetic camera_link attach loop: {e!r}")

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

            try:
                variant = str(s.get("/gil/humanoid/variant") or "h1").lower().strip()
            except Exception:
                variant = "h1"
            is_h12 = variant in ("h1_2", "h1-2", "h12") or variant.startswith("h1_2")
            is_g1 = variant in ("g1", "unitree_g1")
            if (not is_h12) and (not is_g1):
                try:
                    s.set("/gil/camera/fpv_prim_path", "/World/Humanoid/FPVCamera")
                except Exception:
                    pass

            # FPV camera:
            # Prefer the stable synthetic camera path and drive its world pose from dynamic-control in the walker.
            # This avoids inheriting arbitrary link coordinate frames (which can rotate the camera unexpectedly).
            fpv_path_setting = s.get("/gil/camera/fpv_prim_path")
            fpv_path_setting = "" if fpv_path_setting is None else str(fpv_path_setting)
            fpv_path = fpv_path_setting.strip() if fpv_path_setting.strip() else "/World/Humanoid/FPVCamera"
            is_default_fpv = (not fpv_path_setting.strip()) or (fpv_path_setting.strip() == "/World/Humanoid/FPVCamera")
            try:
                carb.log_info(
                    f"[gil.unitree_h1_scene] fpv_camera selected path={fpv_path} "
                    f"default_path={bool(is_default_fpv)}"
                )
            except Exception:
                pass

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

            # Position/rotate the FPV camera only when created; the walker drives its world pose during sim.
            force_pose = s.get("/gil/camera/force_fpv_pose")
            force_pose = True if force_pose is None else bool(force_pose)
            if (created_synth or force_pose) and fpv_path == "/World/Humanoid/FPVCamera":
                xf = UsdGeom.Xformable(fpv_prim)
                # Make the synthetic FPV camera deterministic by clearing any prior xform ops and authoring:
                # - Translate (mount position)
                # - Orient quaternion that maps USD camera frame to robot frame:
                #   USD camera: +X right, +Y up, -Z forward
                #   Desired:    forward = robot +X, up = robot +Z
                try:
                    xf.ClearXformOpOrder()
                except Exception:
                    pass
                xlate = xf.AddTranslateOp()
                orient = xf.AddOrientOp()

                # Allow overrides via settings
                fpv_x = s.get("/gil/camera/fpv_x")
                fpv_y = s.get("/gil/camera/fpv_y")
                fpv_z = s.get("/gil/camera/fpv_z")
                fpv_pos = Gf.Vec3d(
                    0.20 if fpv_x is None else float(fpv_x),
                    0.00 if fpv_y is None else float(fpv_y),
                    1.55 if fpv_z is None else float(fpv_z),
                )
                xlate.Set(fpv_pos)

                # Rotation matrix whose columns are the camera local axes expressed in parent frame:
                # X_cam -> +Y_parent, Y_cam -> +Z_parent, Z_cam -> -X_parent
                m = [
                    [0.0, 0.0, -1.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                ]
                # Optional fine-tuning offsets applied in camera-local axes (degrees).
                # +pitch rotates up around camera +X, +yaw rotates left around camera +Y, +roll rotates clockwise around camera +Z.
                off_pitch = s.get("/gil/camera/fpv_pitch_offset_deg")
                off_yaw = s.get("/gil/camera/fpv_yaw_offset_deg")
                off_roll = s.get("/gil/camera/fpv_roll_offset_deg")
                off_pitch = 0.0 if off_pitch is None else float(off_pitch)
                off_yaw = 0.0 if off_yaw is None else float(off_yaw)
                off_roll = 0.0 if off_roll is None else float(off_roll)

                def _rx(a: float) -> list[list[float]]:
                    a = float(a) * math.pi / 180.0
                    c, s2 = math.cos(a), math.sin(a)
                    return [[1.0, 0.0, 0.0], [0.0, c, -s2], [0.0, s2, c]]

                def _ry(a: float) -> list[list[float]]:
                    a = float(a) * math.pi / 180.0
                    c, s2 = math.cos(a), math.sin(a)
                    return [[c, 0.0, s2], [0.0, 1.0, 0.0], [-s2, 0.0, c]]

                def _rz(a: float) -> list[list[float]]:
                    a = float(a) * math.pi / 180.0
                    c, s2 = math.cos(a), math.sin(a)
                    return [[c, -s2, 0.0], [s2, c, 0.0], [0.0, 0.0, 1.0]]

                def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
                    return [
                        [a[0][0] * b[0][j] + a[0][1] * b[1][j] + a[0][2] * b[2][j] for j in range(3)],
                        [a[1][0] * b[0][j] + a[1][1] * b[1][j] + a[1][2] * b[2][j] for j in range(3)],
                        [a[2][0] * b[0][j] + a[2][1] * b[1][j] + a[2][2] * b[2][j] for j in range(3)],
                    ]

                if abs(off_pitch) > 1e-6 or abs(off_yaw) > 1e-6 or abs(off_roll) > 1e-6:
                    # Apply as intrinsic rotations about camera-local axes: R = R_base * (Rz * Ry * Rx)
                    r_off = _mul(_rz(off_roll), _mul(_ry(off_yaw), _rx(off_pitch)))
                    m = _mul(m, r_off)
                # Convert matrix->quat (wxyz)
                m00, m01, m02 = m[0]
                m10, m11, m12 = m[1]
                m20, m21, m22 = m[2]
                tr = m00 + m11 + m22
                if tr > 0.0:
                    s4 = (tr + 1.0) ** 0.5 * 2.0
                    w = 0.25 * s4
                    x = (m21 - m12) / s4
                    y = (m02 - m20) / s4
                    z = (m10 - m01) / s4
                elif (m00 > m11) and (m00 > m22):
                    s4 = (1.0 + m00 - m11 - m22) ** 0.5 * 2.0
                    w = (m21 - m12) / s4
                    x = 0.25 * s4
                    y = (m01 + m10) / s4
                    z = (m02 + m20) / s4
                elif m11 > m22:
                    s4 = (1.0 + m11 - m00 - m22) ** 0.5 * 2.0
                    w = (m02 - m20) / s4
                    x = (m01 + m10) / s4
                    y = 0.25 * s4
                    z = (m12 + m21) / s4
                else:
                    s4 = (1.0 + m22 - m00 - m11) ** 0.5 * 2.0
                    w = (m10 - m01) / s4
                    x = (m02 + m20) / s4
                    y = (m12 + m21) / s4
                    z = 0.25 * s4
                orient.Set(Gf.Quatf(float(w), Gf.Vec3f(float(x), float(y), float(z))))

            # Isaac Lab H1 maze cameras: ChaseCamera is a world-offset follow cam on the robot root
            # (see gil_isaaclab_tasks maze_env_cfg.py). Stock H1 USD has no onboard cameras.
            chase_path = "/World/Humanoid/ChaseCamera"
            chase_prim = stage.GetPrimAtPath(chase_path)
            if not (chase_prim and chase_prim.IsValid() and chase_prim.IsA(UsdGeom.Camera)):
                chase = UsdGeom.Camera.Define(stage, chase_path)
                chase_prim = chase.GetPrim()
            try:
                UsdGeom.Imageable(chase_prim).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                cam_ch = UsdGeom.Camera(chase_prim)
                cam_ch.CreateFocalLengthAttr().Set(18.0)
                cam_ch.CreateHorizontalApertureAttr().Set(20.955)
            except Exception:
                pass
            try:
                s.set("/gil/camera/chase_prim_path", chase_path)
            except Exception:
                pass

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
            # Enable suite when capture_all is requested (so we can always produce mounted cameras),
            # or when explicitly enabled.
            suite_enabled = s.get("/gil/camera/ensure_robot_suite")
            suite_enabled = False if suite_enabled is None else bool(suite_enabled)
            try:
                cap_all = s.get("/gil/camera/capture_all")
                cap_all = False if cap_all is None else bool(cap_all)
                if cap_all and is_h12:
                    suite_enabled = True
            except Exception:
                pass
            if not is_h12:
                suite_enabled = False
            if suite_enabled:
                body_front = stage.GetPrimAtPath("/World/Humanoid/torso/front_cam")
                if not (body_front and body_front.IsValid()):
                    # Remapped H1 links may live under Joints/ or a discovered torso prim.
                    try:
                        for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Humanoid")):
                            if p.GetName() == "front_cam" and p.IsA(UsdGeom.Camera):
                                body_front = p
                                break
                    except Exception:
                        body_front = None
                if body_front and body_front.IsValid() and body_front.IsA(UsdGeom.Camera):
                    bp = body_front.GetPath().pathString
                    if "/camera_link/" in bp:
                        carb.log_info(
                            f"[gil.unitree_h1_scene] skip gil_cameras; using USD camera_link suite fpv={bp}"
                        )
                        suite_enabled = False
            if suite_enabled:
                try:
                    # Keep these cameras under the humanoid root (not pelvis) so we can drive them in world
                    # coordinates from the walker without inheriting link coordinate frames.
                    suite_group = "/World/Humanoid/gil_cameras"
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

                    # Initial poses only; the walker will drive these cameras in world space while sim runs.
                    _ensure_suite_cam("front_cam", (0.28, 0.00, 0.85), (0.0, 0.0, 0.0))
                    _ensure_suite_cam("world_cam", (-1.80, 0.00, 1.20), (0.0, 0.0, 0.0))
                    _ensure_suite_cam("left_wrist_cam", (0.35, -0.25, 0.65), (0.0, 0.0, 0.0))
                    _ensure_suite_cam("right_wrist_cam", (0.35, 0.25, 0.65), (0.0, 0.0, 0.0))

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

    async def _attach_synthetic_camera_link_loop(self) -> None:
        """
        Keep a synthesized `/World/Humanoid/camera_link` aligned to a moving physics link (torso/pelvis).

        This is only enabled when `/World/Humanoid/camera_link` was created by us (customData
        `gil:synthetic_camera_parent=true`). Without this, cameras under `camera_link/*` can be stuck near
        spawn when the humanoid root prim does not move with physics.
        """
        try:
            carb.log_info("[gil.unitree_h1_scene] camera_link attach loop started")
        except Exception:
            pass
        while True:
            # Do not rely on `next_update_async()` here: in some Kit startup/headless sequences it can stall,
            # which makes the camera rig appear "not attached" even though the task is running.
            await asyncio.sleep(1.0 / 60.0)
            try:
                stage = omni.usd.get_context().get_stage()
                if stage is None:
                    continue

                s = carb.settings.get_settings()
                enabled = s.get("/gil/camera/attach_synthetic_camera_link")
                enabled = True if enabled is None else bool(enabled)
                if not enabled:
                    continue
                # Rate-limited heartbeat so logs confirm this loop is actually running.
                try:
                    now_hb = float(asyncio.get_event_loop().time())
                    last_hb = float(getattr(self, "_cam_link_last_hb_t", 0.0) or 0.0)
                    if (now_hb - last_hb) >= 2.0:
                        self._cam_link_last_hb_t = now_hb
                        carb.log_info("[gil.unitree_h1_scene] camera_link attach loop tick")
                except Exception:
                    pass

                humanoid = stage.GetPrimAtPath("/World/Humanoid")
                cam_link = stage.GetPrimAtPath("/World/Humanoid/camera_link")
                if not (humanoid and humanoid.IsValid() and cam_link and cam_link.IsValid()):
                    continue

                # Only touch camera_link if it was synthesized by us.
                # Prefer the explicit boolean we set at creation-time; customData can be unreliable across composition.
                is_synth = False
                try:
                    is_synth = bool(getattr(self, "_synthetic_camera_link", False))
                except Exception:
                    is_synth = False
                if not is_synth:
                    try:
                        if not bool(cam_link.GetCustomData().get("gil:synthetic_camera_parent", False)):
                            continue
                    except Exception:
                        continue

                # Prefer torso if present, else pelvis.
                src = stage.GetPrimAtPath("/World/Humanoid/torso")
                if not (src and src.IsValid()):
                    src = stage.GetPrimAtPath("/World/Humanoid/pelvis")
                if not (src and src.IsValid()):
                    continue

                hxf = UsdGeom.Xformable(humanoid)
                cxf = UsdGeom.Xformable(cam_link)
                t = Usd.TimeCode.Default()

                mh = hxf.ComputeLocalToWorldTransform(t)
                try:
                    mh_inv = mh.GetInverse()
                except Exception:
                    mh_inv = mh.GetOrthonormalInverse()

                # Prefer dynamic-control rigid-body pose (USD transforms can be static while physics moves).
                src_pos_world: Gf.Vec3d | None = None
                # local rotation matrix columns (x,y,z axes expressed in humanoid local frame)
                local_axes: tuple[Gf.Vec3d, Gf.Vec3d, Gf.Vec3d] | None = None
                try:
                    from omni.isaac.dynamic_control import _dynamic_control  # type: ignore
                    from omni import timeline as omni_timeline

                    if getattr(self, "_dc", None) is None:
                        try:
                            self._dc = _dynamic_control.acquire_dynamic_control_interface()
                            self._dc_cam_body = None
                            self._dc_cam_body_path = ""
                        except Exception:
                            self._dc = None

                    if getattr(self, "_dc", None) is not None:
                        # Only attempt to bind dynamic-control bodies once physics is running,
                        # otherwise DC will emit noisy "Failed to register rigid body" errors.
                        try:
                            tl = getattr(self, "_timeline", None)
                        except Exception:
                            tl = None
                        if tl is None:
                            try:
                                self._timeline = omni_timeline.get_timeline_interface()
                                tl = self._timeline
                            except Exception:
                                tl = None

                        now_bind = float(asyncio.get_event_loop().time())
                        retry_until = float(getattr(self, "_dc_cam_bind_retry_until", 0.0) or 0.0)

                        # Try to bind to a *moving* rigid body. Avoid caching the static humanoid root.
                        # Re-try binding if we previously latched onto a bad handle (e.g. /World/Humanoid).
                        bad_cached = False
                        try:
                            bad_cached = str(getattr(self, "_dc_cam_body_path", "") or "") in ("", "/World/Humanoid")
                        except Exception:
                            bad_cached = True
                        if (
                            (tl is not None and bool(getattr(tl, "is_playing")()) )
                            and now_bind >= retry_until
                            and (getattr(self, "_dc_cam_body", None) is None or bad_cached)
                        ):
                            # Prefer the actual src prim path (pelvis/torso) then common link names.
                            cand_list = [
                                str(src.GetPath()),
                                "/World/Humanoid/pelvis",
                                "/World/Humanoid/torso_link",
                                "/World/Humanoid/torso",
                                "/World/Humanoid/base_link",
                                "/World/Humanoid/base",
                            ]
                            self._dc_cam_body = None
                            self._dc_cam_body_path = ""
                            for cand in cand_list:
                                try:
                                    h = self._dc.get_rigid_body(cand)
                                    if h:
                                        self._dc_cam_body = h
                                        self._dc_cam_body_path = str(cand)
                                        try:
                                            carb.log_info(f"[gil.unitree_h1_scene] camera_link bound dc_body={self._dc_cam_body_path}")
                                        except Exception:
                                            pass
                                        break
                                except Exception:
                                    continue
                            if getattr(self, "_dc_cam_body", None) is None:
                                # Backoff to avoid hammering DC during startup.
                                self._dc_cam_bind_retry_until = float(now_bind) + 1.0

                    if getattr(self, "_dc", None) is not None and getattr(self, "_dc_cam_body", None) is not None:
                        tf = self._dc.get_rigid_body_pose(self._dc_cam_body)
                        p = getattr(tf, "p", None)
                        q = getattr(tf, "r", None)
                        if p is not None:
                            px = getattr(p, "x", None)
                            if px is None:
                                src_pos_world = Gf.Vec3d(float(p[0]), float(p[1]), float(p[2]))
                            else:
                                src_pos_world = Gf.Vec3d(float(p.x), float(p.y), float(p.z))

                        if q is not None and src_pos_world is not None:
                            qw = getattr(q, "w", None)
                            if qw is None:
                                qw, qx, qy, qz = float(q[0]), float(q[1]), float(q[2]), float(q[3])
                            else:
                                qx = float(getattr(q, "x", 0.0))
                                qy = float(getattr(q, "y", 0.0))
                                qz = float(getattr(q, "z", 0.0))
                                qw = float(qw)

                            # Quaternion (w,x,y,z) -> world rotation matrix columns.
                            xx, yy, zz = qx * qx, qy * qy, qz * qz
                            xy, xz, yz = qx * qy, qx * qz, qy * qz
                            wx, wy, wz = qw * qx, qw * qy, qw * qz
                            # Column vectors: R * e_i
                            xw = Gf.Vec3d(1.0 - 2.0 * (yy + zz), 2.0 * (xy + wz), 2.0 * (xz - wy))
                            yw = Gf.Vec3d(2.0 * (xy - wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz + wx))
                            zw = Gf.Vec3d(2.0 * (xz + wy), 2.0 * (yz - wx), 1.0 - 2.0 * (xx + yy))

                            # Transform world axes into humanoid-local axes.
                            xl = mh_inv.TransformDir(xw)
                            yl = mh_inv.TransformDir(yw)
                            zl = mh_inv.TransformDir(zw)
                            local_axes = (xl, yl, zl)
                except Exception:
                    pass

                # Fallback to USD pose if dynamic-control isn't available.
                if src_pos_world is None or local_axes is None:
                    sxf = UsdGeom.Xformable(src)
                    ms = sxf.ComputeLocalToWorldTransform(t)
                    src_pos_world = ms.ExtractTranslation()
                    xw = ms.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0))
                    yw = ms.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
                    zw = ms.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0))
                    local_axes = (mh_inv.TransformDir(xw), mh_inv.TransformDir(yw), mh_inv.TransformDir(zw))

                # Author local translate/orient on camera_link.
                xlate = None
                orient = None
                try:
                    ops = getattr(self, "_cam_link_ops", None)
                    if ops and len(ops) == 2:
                        xlate, orient = ops  # type: ignore[misc]
                except Exception:
                    xlate = orient = None

                if xlate is None or orient is None:
                    # Make the transform deterministic: wipe any previous ops and author only translate+orient.
                    try:
                        cxf.ClearXformOpOrder()
                    except Exception:
                        pass
                    xlate = cxf.AddTranslateOp()
                    orient = cxf.AddOrientOp()
                    try:
                        self._cam_link_ops = (xlate, orient)
                        # Debug for first ~10s after the first successful bind.
                        if getattr(self, "_cam_link_debug_until_s", None) is None:
                            self._cam_link_debug_until_s = float(asyncio.get_event_loop().time()) + 10.0
                    except Exception:
                        pass

                # Position: humanoid-local point
                pl = mh_inv.Transform(src_pos_world)
                xlate.Set(Gf.Vec3d(float(pl[0]), float(pl[1]), float(pl[2])))

                # Orientation: convert humanoid-local rotation matrix to quaternion (wxyz)
                xl, yl, zl = local_axes
                m00, m10, m20 = float(xl[0]), float(xl[1]), float(xl[2])
                m01, m11, m21 = float(yl[0]), float(yl[1]), float(yl[2])
                m02, m12, m22 = float(zl[0]), float(zl[1]), float(zl[2])
                tr = m00 + m11 + m22
                if tr > 0.0:
                    s4 = (tr + 1.0) ** 0.5 * 2.0
                    qw = 0.25 * s4
                    qx = (m21 - m12) / s4
                    qy = (m02 - m20) / s4
                    qz = (m10 - m01) / s4
                elif (m00 > m11) and (m00 > m22):
                    s4 = (1.0 + m00 - m11 - m22) ** 0.5 * 2.0
                    qw = (m21 - m12) / s4
                    qx = 0.25 * s4
                    qy = (m01 + m10) / s4
                    qz = (m02 + m20) / s4
                elif m11 > m22:
                    s4 = (1.0 + m11 - m00 - m22) ** 0.5 * 2.0
                    qw = (m02 - m20) / s4
                    qx = (m01 + m10) / s4
                    qy = 0.25 * s4
                    qz = (m12 + m21) / s4
                else:
                    s4 = (1.0 + m22 - m00 - m11) ** 0.5 * 2.0
                    qw = (m10 - m01) / s4
                    qx = (m02 + m20) / s4
                    qy = (m12 + m21) / s4
                    qz = 0.25 * s4
                orient.Set(Gf.Quatf(float(qw), Gf.Vec3f(float(qx), float(qy), float(qz))))

                # Lightweight startup debug: confirm camera_link world pose changes while robot moves.
                try:
                    until = getattr(self, "_cam_link_debug_until_s", None)
                    now = float(asyncio.get_event_loop().time())
                    if until is not None and now <= float(until):
                        mw = UsdGeom.Xformable(cam_link).ComputeLocalToWorldTransform(t)
                        pw = mw.ExtractTranslation()
                        carb.log_info(
                            f"[gil.unitree_h1_scene] cam_link_world=({float(pw[0]):.3f},{float(pw[1]):.3f},{float(pw[2]):.3f}) "
                            f"src_world=({float(src_pos_world[0]):.3f},{float(src_pos_world[1]):.3f},{float(src_pos_world[2]):.3f})"
                        )
                        # Only print occasionally (reduce spam).
                        self._cam_link_debug_until_s = float(until) if (now + 1.0) < float(until) else None
                except Exception:
                    pass
            except Exception as e:
                # Rate-limited diagnostics: this loop is critical for "cameras stuck at spawn" bugs.
                try:
                    now2 = float(asyncio.get_event_loop().time())
                    last_t = float(getattr(self, "_cam_link_last_err_t", 0.0) or 0.0)
                    if (now2 - last_t) >= 2.0:
                        self._cam_link_last_err_t = now2
                        carb.log_warn(f"[gil.unitree_h1_scene] camera_link attach loop error: {e!r}")
                except Exception:
                    pass
                continue

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
            if self._cam_link_task is not None:
                self._cam_link_task.cancel()
        except Exception:
            pass
        try:
            if self._attach_sensors_task is not None:
                self._attach_sensors_task.cancel()
        except Exception:
            pass


