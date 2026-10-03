from __future__ import annotations

import pytest

from gil.world.backends import EXECUTE_BACKEND, IMAGINE_BACKEND, UNTRUSTED_SCENE_SOURCES, stack_roles
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.maze3d import generate_maze
from gil.world.twin import UnicycleTwin

pytestmark = pytest.mark.phase5


def test_isaac_is_the_execute_backend_and_threejs_cannot_command_motors():
    roles = stack_roles()
    assert EXECUTE_BACKEND == "isaac_sim"
    assert IMAGINE_BACKEND == "isaac_scene_copy"
    assert roles["viewport"] == "isaac_sim"
    assert roles["retired_viewport"] == "gil_frontend Three.js"
    assert "threejs" in UNTRUSTED_SCENE_SOURCES
    assert "cosmos" in UNTRUSTED_SCENE_SOURCES


def test_maze_generation_is_deterministic():
    a = generate_maze()
    b = generate_maze()
    assert len(a.walls) == len(b.walls) > 40
    assert [(w.cx, w.cy, w.sx, w.sy) for w in a.walls] == [(w.cx, w.cy, w.sx, w.sy) for w in b.walls]
    sx, sy = a.cell_center(0, 0)
    assert a.start == pytest.approx((sx, sy, 0.0))


def test_start_cell_is_free_and_a_wall_is_solid():
    maze = generate_maze()
    assert maze.capsule_hits_wall(*maze.start[:2]) is False
    wall = maze.walls[0]
    assert maze.capsule_hits_wall(wall.cx, wall.cy) is True


def test_seed0_has_a_corridor_from_spawn_to_isaac_goal():
    maze = generate_maze()
    path = maze.shortest_cell_path(maze.start[:2], maze.goal[:2])
    assert path is not None
    assert path[0] == (0, 0)
    assert len(path) > 2


def test_sealed_spawn_has_no_path():
    maze = generate_maze().sealed_start()
    assert maze.neighbors(0, 0) == []
    assert maze.shortest_cell_path(maze.start[:2], maze.goal[:2]) is None
    assert maze.capsule_hits_wall(*maze.start[:2]) is False


def test_kinematic3d_finds_a_physically_valid_escape():
    maze = generate_maze()
    wm = Kinematic3DWorldModel(maze=maze, dt=0.2)
    from gil.core.types import Goal

    dreams = wm.dream(maze.to_observation(), Goal(x=maze.goal[0], y=maze.goal[1], radius=0.45), n=8)
    assert any(rollout.success and rollout.physics_ok for rollout in dreams)


def test_kinematic3d_cannot_leave_a_sealed_cell():
    maze = generate_maze().sealed_start()
    wm = Kinematic3DWorldModel(maze=maze, dt=0.2)
    from gil.core.types import Goal

    twin = UnicycleTwin()
    twin.pose.x, twin.pose.y = maze.start[0], maze.start[1]
    dreams = wm.dream(maze.to_observation(), Goal(x=maze.goal[0], y=maze.goal[1], radius=0.45), n=8)
    assert all(not rollout.success for rollout in dreams)
    assert twin.distance_to(maze.goal[0], maze.goal[1]) > 4.0
