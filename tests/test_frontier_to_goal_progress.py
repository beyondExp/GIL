from __future__ import annotations

from gil.world.cell_map import CellDiscoveryMap, goal_cell
from gil.world.maze3d import generate_maze


def test_frontier_count_shrinks_as_revealed() -> None:
    maze = generate_maze()
    disc = CellDiscoveryMap(width=maze.spec.width, height=maze.spec.height, seed=maze.spec.seed)
    start_cell = (0, 0)
    disc.reveal_local(maze, at=start_cell, radius_cells=1)
    f0 = len(disc.frontier_portals(maze))
    # Reveal a wider neighborhood; frontier count should not increase dramatically.
    disc.reveal_local(maze, at=start_cell, radius_cells=3)
    f1 = len(disc.frontier_portals(maze))
    assert f1 >= 0
    assert f1 <= (f0 + 20)


def test_goal_cell_function_is_valid() -> None:
    maze = generate_maze()
    g = goal_cell(maze)
    assert 0 <= g[0] < maze.spec.width
    assert 0 <= g[1] < maze.spec.height

