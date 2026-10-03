from __future__ import annotations

from typing import Literal

# Live roles. Imagination copies the environment; Isaac is the only motor body.
ExecuteBackend = Literal["isaac_sim"]
ImagineBackend = Literal["scene3d", "isaac_clone", "isaac_scene_copy"]
SceneGenerator = Literal["maze_dfs", "marble", "nurec", "replicator", "cosmos_transfer"]

EXECUTE_BACKEND: ExecuteBackend = "isaac_sim"
IMAGINE_BACKEND: ImagineBackend = "isaac_scene_copy"

# Not wired. Same orchestrator protocol when they land.
FUTURE_SCENE_GENERATORS: tuple[SceneGenerator, ...] = (
    "marble",
    "nurec",
    "replicator",
    "cosmos_transfer",
)

# These may propose geometry or pixels. They never publish cmd_vel.
UNTRUSTED_SCENE_SOURCES = frozenset(
    {"marble", "nurec", "cosmos", "cosmos_transfer", "groot", "gemini", "threejs", "replicator"}
)


def stack_roles() -> dict[str, str]:
    """Who does what. Isaac Sim is the viewport and PhysX body; Three.js is not."""
    return {
        "execute": "isaac_sim",
        "viewport": "isaac_sim",
        "imagine": "isaac_scene_copy",
        "imagine_geometry": "gil.maze_env seed 0 (copied into gil.world.maze3d)",
        "retired_viewport": "gil_frontend Three.js",
        "future_scene": ",".join(FUTURE_SCENE_GENERATORS),
        "pipeline": (
            "[prompt|video] -> [Marble|NuRec|Replicator] -> [USD + collision] -> [Isaac PhysX]; "
            "Cosmos Transfer may augment cameras only; gil_controls still gates motors"
        ),
    }
