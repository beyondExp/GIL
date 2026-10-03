import asyncio
import random
from dataclasses import dataclass

import carb
import omni.ext
import omni.kit.app
import omni.usd
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade


@dataclass(frozen=True)
class MazeConfig:
    # Maze grid (cells). Walls are placed on cell boundaries.
    width: int = 9
    height: int = 9
    # Wider corridors by default (see also MazeSpec in `src/gil/world/maze3d.py`).
    cell_size: float = 1.4  # meters

    # Wall geometry
    wall_thickness: float = 0.05
    wall_height: float = 1.0

    # Placement
    origin_x: float = -4.0
    origin_y: float = -4.0
    z: float = 0.5  # wall center z

    # Randomness
    seed: int = 0


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        carb.log_info(f"[gil.maze_env] startup ext_id={ext_id}")
        self._task = asyncio.ensure_future(self._spawn_when_ready())

    @staticmethod
    def _find_prim(stage: Usd.Stage, path: str):
        """Walk children instead of Stage.GetPrimAtPath (pxr Path/str mismatch on this Kit)."""
        cur = stage.GetPseudoRoot()
        for name in [n for n in str(path).strip("/").split("/") if n]:
            nxt = None
            for child in cur.GetChildren():
                if child.GetName() == name:
                    nxt = child
                    break
            if nxt is None:
                return None
            cur = nxt
        return cur

    def _ensure_ground(self, stage: Usd.Stage) -> None:
        """
        Ensure a large static ground collider exists.

        Isaac experiences vary: some ship with a ground plane, some don't. Without a ground collider, the humanoid
        will immediately fall forever (or appear to "ragdoll and reset" if a supervisor is watching base z).
        """
        try:
            from isaacsim.core.utils.prims import create_prim, get_prim_at_path, is_prim_path_valid

            ground_path = "/World/Ground"
            if is_prim_path_valid(ground_path):
                return

            mat_path = "/World/Materials/GroundMaterial"
            if not is_prim_path_valid(mat_path):
                create_prim(mat_path, "Material")
            mat_prim = get_prim_at_path(mat_path)
            try:
                UsdPhysics.MaterialAPI.Apply(mat_prim)
            except Exception:
                pass
            try:
                pm = PhysxSchema.PhysxMaterialAPI.Apply(mat_prim)
                pm.CreateStaticFrictionAttr().Set(1.2)
                pm.CreateDynamicFrictionAttr().Set(1.1)
                pm.CreateRestitutionAttr().Set(0.0)
            except Exception:
                pass

            thickness = 0.10
            create_prim(
                ground_path,
                "Cube",
                translation=(0.0, 0.0, -thickness / 2.0),
                scale=(200.0, 200.0, float(thickness)),
                attributes={"size": 1.0},
            )
            prim = get_prim_at_path(ground_path)
            col = UsdPhysics.CollisionAPI.Apply(prim)
            col.CreateCollisionEnabledAttr(True)
            PhysxSchema.PhysxCollisionAPI.Apply(prim)
            try:
                UsdShade.MaterialBindingAPI(prim).Bind(UsdShade.Material(mat_prim))
            except Exception:
                pass
            carb.log_warn("[gil.maze_env] Ground collider ensured at /World/Ground")
        except Exception as e:
            carb.log_warn(f"[gil.maze_env] Failed to ensure ground collider: {e!r}")

    async def _spawn_when_ready(self) -> None:
        app = omni.kit.app.get_app()
        # wait for stage
        stage: Usd.Stage | None = None
        for i in range(600):
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                break
            # Some Isaac Sim experiences create the stage late. If no stage exists after a short grace
            # period, proactively create one so physics can initialize before other extensions need it.
            if i == 30:
                try:
                    omni.usd.get_context().new_stage()
                except Exception:
                    pass
            await app.next_update_async()
        if stage is None:
            carb.log_error("[gil.maze_env] No USD stage available after waiting")
            return

        cfg = self._read_cfg()
        carb.log_info(f"[gil.maze_env] Spawning maze width={cfg.width} height={cfg.height} seed={cfg.seed}")

        # Allow running the humanoid in open space (no maze walls) while still ensuring a PhysicsScene exists.
        try:
            s = carb.settings.get_settings()
            enabled = s.get("/gil/maze/enabled")
            if enabled is None:
                enabled = True
            enabled = bool(enabled)
        except Exception:
            enabled = True

        # Ensure a physics scene exists so locomotion policies can drive the robot.
        # If no `UsdPhysics.Scene` is attached, Isaac may error with:
        #   "Failed to create simulation view: no active physics scene found"
        try:
            existing = None
            try:
                for p in Usd.PrimRange(stage.GetPseudoRoot()):
                    try:
                        if UsdPhysics.Scene(p):
                            existing = p
                            break
                    except Exception:
                        continue
            except Exception:
                existing = None

            if existing is None or not existing.IsValid():
                # Ensure a physics scene at the conventional Isaac paths.
                #
                # In practice we've seen different Isaac components look for a scene at either:
                # - /physicsScene
                # - /World/physicsScene
                # Defining both is cheap and avoids "no active physics scene found" when one path is ignored.
                ensured_paths = []
                for p in ("/physicsScene", "/World/physicsScene"):
                    try:
                        scene_path = Sdf.Path(p)
                        scene = UsdPhysics.Scene.Define(stage, scene_path)
                        scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))
                        scene.CreateGravityMagnitudeAttr().Set(9.81)
                        try:
                            PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
                        except Exception:
                            pass
                        ensured_paths.append(p)
                    except Exception:
                        continue
                carb.log_warn(f"[gil.maze_env] PhysicsScene ensured at {ensured_paths}")
                try:
                    carb.settings.get_settings().set("/gil/physics_scene_ready", True)
                except Exception:
                    pass
                # Give PhysX a few frames to notice the new scene prim before anything tries to
                # create tensors SimulationView (otherwise Isaac can report "no active physics scene found").
                for _ in range(10):
                    await app.next_update_async()
            else:
                carb.log_warn(f"[gil.maze_env] Using existing PhysicsScene at {existing.GetPath()}")
                try:
                    carb.settings.get_settings().set("/gil/physics_scene_ready", True)
                except Exception:
                    pass
        except Exception as e:
            carb.log_warn(f"[gil.maze_env] Failed to ensure PhysicsScene: {e!r}")

        # Ensure a floor collider exists even in open-space mode.
        self._ensure_ground(stage)

        if not enabled:
            carb.log_warn("[gil.maze_env] Maze disabled (/gil/maze/enabled=false). Skipping wall spawn.")
            return

        try:
            self._build_maze(stage, cfg)
        except Exception as e:
            carb.log_error(f"[gil.maze_env] Maze spawn failed: {e!r}")
            return
        last_sig = (cfg.width, cfg.height, cfg.seed, round(cfg.cell_size, 4))
        while True:
            await app.next_update_async()
            try:
                s = carb.settings.get_settings()
                rebuild = bool(s.get("/gil/maze/rebuild") or False)
            except Exception:
                rebuild = False
            cfg2 = self._read_cfg()
            sig = (cfg2.width, cfg2.height, cfg2.seed, round(cfg2.cell_size, 4))
            if rebuild or sig != last_sig:
                try:
                    carb.settings.get_settings().set("/gil/maze/rebuild", False)
                except Exception:
                    pass
                self._build_maze(stage, cfg2)
                last_sig = sig
                carb.log_info(f"[gil.maze_env] Maze rebuilt width={cfg2.width} height={cfg2.height} seed={cfg2.seed}")

    def _build_maze(self, stage: Usd.Stage, cfg: MazeConfig) -> None:
        from isaacsim.core.utils.prims import create_prim, delete_prim, is_prim_path_valid

        if is_prim_path_valid("/World/Maze"):
            try:
                delete_prim("/World/Maze")
            except Exception:
                pass
        create_prim("/World/Maze", "Xform")

        rng = random.Random(cfg.seed)
        visited = [[False for _ in range(cfg.width)] for _ in range(cfg.height)]
        v_walls = [[True for _ in range(cfg.width + 1)] for _ in range(cfg.height)]
        h_walls = [[True for _ in range(cfg.width)] for _ in range(cfg.height + 1)]

        def neighbors(cx: int, cy: int):
            dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            rng.shuffle(dirs)
            for dx, dy in dirs:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < cfg.width and 0 <= ny < cfg.height and not visited[ny][nx]:
                    yield nx, ny

        stack = [(0, 0)]
        visited[0][0] = True
        while stack:
            cx, cy = stack[-1]
            nxt = None
            for nx, ny in neighbors(cx, cy):
                nxt = (nx, ny)
                break
            if nxt is None:
                stack.pop()
                continue
            nx, ny = nxt
            if nx == cx + 1:
                v_walls[cy][cx + 1] = False
            elif nx == cx - 1:
                v_walls[cy][cx] = False
            elif ny == cy + 1:
                h_walls[cy + 1][cx] = False
            elif ny == cy - 1:
                h_walls[cy][cx] = False
            visited[ny][nx] = True
            stack.append((nx, ny))

        h_walls[0][0] = False
        h_walls[cfg.height][cfg.width - 1] = False

        prim_idx = 0
        for y in range(cfg.height):
            for x in range(cfg.width + 1):
                if not v_walls[y][x]:
                    continue
                wx = cfg.origin_x + x * cfg.cell_size
                wy = cfg.origin_y + (y + 0.5) * cfg.cell_size
                self._spawn_wall(
                    stage,
                    Sdf.Path(f"/World/Maze/wall_v_{prim_idx}"),
                    center=(wx, wy, cfg.z),
                    size=(cfg.wall_thickness, cfg.cell_size + cfg.wall_thickness, cfg.wall_height),
                )
                prim_idx += 1
        for y in range(cfg.height + 1):
            for x in range(cfg.width):
                if not h_walls[y][x]:
                    continue
                wx = cfg.origin_x + (x + 0.5) * cfg.cell_size
                wy = cfg.origin_y + y * cfg.cell_size
                self._spawn_wall(
                    stage,
                    Sdf.Path(f"/World/Maze/wall_h_{prim_idx}"),
                    center=(wx, wy, cfg.z),
                    size=(cfg.cell_size + cfg.wall_thickness, cfg.wall_thickness, cfg.wall_height),
                )
                prim_idx += 1
        # Use WARN so it shows up in default Isaac log configs.
        carb.log_warn(
            f"[gil.maze_env] Maze spawned walls={prim_idx} "
            f"wall_h={cfg.wall_height} cell={cfg.cell_size} origin=({cfg.origin_x},{cfg.origin_y}) z={cfg.z}"
        )

    def _read_cfg(self) -> MazeConfig:
        s = carb.settings.get_settings()

        def gi(key: str, default: int) -> int:
            v = s.get(key)
            return default if v is None else int(v)

        def gf(key: str, default: float) -> float:
            v = s.get(key)
            return default if v is None else float(v)

        return MazeConfig(
            width=max(3, gi("/gil/maze/width", 9)),
            height=max(3, gi("/gil/maze/height", 9)),
            cell_size=max(0.2, gf("/gil/maze/cell_size", 1.4)),
            wall_thickness=max(0.02, gf("/gil/maze/wall_thickness", 0.05)),
            wall_height=max(0.2, gf("/gil/maze/wall_height", 1.0)),
            origin_x=gf("/gil/maze/origin_x", -4.0),
            origin_y=gf("/gil/maze/origin_y", -4.0),
            z=gf("/gil/maze/z", 0.5),
            seed=gi("/gil/maze/seed", 0),
        )

    def _spawn_wall(self, stage: Usd.Stage, path, center: tuple[float, float, float], size: tuple[float, float, float]) -> None:
        from isaacsim.core.utils.prims import create_prim, get_prim_at_path

        prim_path = str(path)
        create_prim(
            prim_path,
            "Cube",
            translation=center,
            scale=size,
            attributes={"size": 1.0},
        )
        prim = get_prim_at_path(prim_path)
        col = UsdPhysics.CollisionAPI.Apply(prim)
        col.CreateCollisionEnabledAttr(True)
        PhysxSchema.PhysxCollisionAPI.Apply(prim)
        # Best-effort semantics so Replicator instance/semantic masks + bbox annotators can
        # group and name maze geometry (needed for GT export pipelines like WorldSculpt).
        try:
            from omni.isaac.core.utils.semantics import add_update_semantics  # type: ignore

            add_update_semantics(prim, "maze_wall")
        except Exception:
            # Semantics utilities aren't available in all Kit/Isaac configs; ignore.
            pass

    def on_shutdown(self) -> None:
        carb.log_info("[gil.maze_env] shutdown")







