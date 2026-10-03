from __future__ import annotations

from gil.world.cell_map import CellDiscoveryMap, cell_from_world, goal_cell
from gil.world.maze3d import generate_maze


def test_cell_discovery_reveal_and_frontiers() -> None:
    maze = generate_maze()
    m = CellDiscoveryMap(width=maze.spec.width, height=maze.spec.height, seed=maze.spec.seed)
    start = cell_from_world(maze, x=maze.start[0], y=maze.start[1])
    assert start not in m.known_cells
    new = m.reveal_local(maze, at=start, radius_cells=1)
    assert new >= 1
    assert start in m.known_cells
    frontiers = m.frontier_portals(maze)
    assert isinstance(frontiers, list)
    assert len(frontiers) >= 1

    g = goal_cell(maze)
    # Goal cell is usually not known after a single reveal.
    assert (g in m.known_cells) is False


def test_shortest_path_known_requires_discovery() -> None:
    maze = generate_maze()
    m = CellDiscoveryMap(width=maze.spec.width, height=maze.spec.height, seed=maze.spec.seed)
    start = cell_from_world(maze, x=maze.start[0], y=maze.start[1])
    goal = goal_cell(maze)
    assert m.shortest_path_known(start=start, goal=goal) is None
    # Reveal a wider neighborhood; still not guaranteed to include goal.
    m.reveal_local(maze, at=start, radius_cells=3)
    if goal in m.known_cells:
        path = m.shortest_path_known(start=start, goal=goal)
        assert path is not None and path[0] == start and path[-1] == goal

