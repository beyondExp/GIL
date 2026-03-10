import asyncio
import random
from dataclasses import dataclass

import carb
import omni.ext
import omni.kit.app
import omni.usd
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics


@dataclass(frozen=True)
class MazeConfig:
    # Maze grid (cells). Walls are placed on cell boundaries.
    width: int = 9
    height: int = 9
    cell_size: float = 0.9  # meters

    # Wall geometry
    wall_thickness: float = 0.08
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

    async def _spawn_when_ready(self) -> None:
        app = omni.kit.app.get_app()
        # wait for stage
        stage: Usd.Stage | None = None
        for _ in range(600):
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                break
            await app.next_update_async()
        if stage is None:
            carb.log_error("[gil.maze_env] No USD stage available after waiting")
            return

        cfg = self._read_cfg()
        carb.log_info(f"[gil.maze_env] Spawning maze width={cfg.width} height={cfg.height} seed={cfg.seed}")

        # Clean previous maze prim if present (so restarts don't stack walls)
        root_path = Sdf.Path("/World/Maze")
        if stage.GetPrimAtPath(root_path):
            stage.RemovePrim(root_path)

        maze_root = UsdGeom.Xform.Define(stage, root_path)
        maze_root.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))

        # Generate maze walls using randomized DFS on grid.
        # We model walls between cells; start with all walls present, then carve.
        rng = random.Random(cfg.seed)
        visited = [[False for _ in range(cfg.width)] for _ in range(cfg.height)]
        # walls: True means wall exists
        v_walls = [[True for _ in range(cfg.width + 1)] for _ in range(cfg.height)]  # vertical boundaries
        h_walls = [[True for _ in range(cfg.width)] for _ in range(cfg.height + 1)]  # horizontal boundaries

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
            # remove wall between (cx,cy) and (nx,ny)
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

        # Add an entrance and exit for “go-to-goal” tasks (simple openings).
        h_walls[0][0] = False  # entrance bottom-left
        h_walls[cfg.height][cfg.width - 1] = False  # exit top-right

        # Build walls as thin boxes.
        prim_idx = 0
        # vertical walls
        for y in range(cfg.height):
            for x in range(cfg.width + 1):
                if not v_walls[y][x]:
                    continue
                # wall along Y axis at x boundary, spanning one cell
                wx = cfg.origin_x + x * cfg.cell_size
                wy = cfg.origin_y + (y + 0.5) * cfg.cell_size
                self._spawn_wall(
                    stage,
                    Sdf.Path(f"/World/Maze/wall_v_{prim_idx}"),
                    center=(wx, wy, cfg.z),
                    size=(cfg.wall_thickness, cfg.cell_size + cfg.wall_thickness, cfg.wall_height),
                )
                prim_idx += 1
        # horizontal walls
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

        carb.log_info(f"[gil.maze_env] Maze spawned with {prim_idx} wall prims")

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
            cell_size=max(0.2, gf("/gil/maze/cell_size", 0.9)),
            wall_thickness=max(0.02, gf("/gil/maze/wall_thickness", 0.08)),
            wall_height=max(0.2, gf("/gil/maze/wall_height", 1.0)),
            origin_x=gf("/gil/maze/origin_x", -4.0),
            origin_y=gf("/gil/maze/origin_y", -4.0),
            z=gf("/gil/maze/z", 0.5),
            seed=gi("/gil/maze/seed", 0),
        )

    def _spawn_wall(self, stage: Usd.Stage, path: Sdf.Path, center: tuple[float, float, float], size: tuple[float, float, float]) -> None:
        # We use a Cube prim with scale to become a box.
        prim = UsdGeom.Cube.Define(stage, path)
        prim.CreateSizeAttr(1.0)
        xf = UsdGeom.Xformable(prim.GetPrim())
        xf.AddTranslateOp().Set(Gf.Vec3d(*center))
        xf.AddScaleOp().Set(Gf.Vec3f(size[0], size[1], size[2]))

        # collision only (static)
        col = UsdPhysics.CollisionAPI.Apply(prim.GetPrim())
        col.CreateCollisionEnabledAttr(True)
        PhysxSchema.PhysxCollisionAPI.Apply(prim.GetPrim())

    def on_shutdown(self) -> None:
        carb.log_info("[gil.maze_env] shutdown")







