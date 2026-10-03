from __future__ import annotations

import pytest

from gil.core.profiles import load_profile
from gil.world.map import SceneMap

pytestmark = pytest.mark.phase4


def test_calibrated_map_locates_objects_and_paths():
    scene = SceneMap(profile=load_profile("unitree_h1_sim"), cell_m=0.25)
    snap = scene.ingest(
        {
            "base": {"x": 0.0, "y": 0.0},
            "objects": [
                {"label": "red cube", "x": 1.0, "y": 0.0},
                {"label": "bin", "x": 1.2, "y": 0.4},
            ],
        }
    )
    assert snap.calibrated is True
    assert snap.coverage >= 0.5
    found = scene.locate("red cube")
    assert found is not None
    assert found.x == 1.0
    path = scene.shortest_path((0.0, 0.0), (0.0, 1.0))
    assert path is not None
    assert path[0] == scene._cell(0.0, 0.0)


def test_uncalibrated_hardware_map_is_not_motion_authority():
    scene = SceneMap(profile=load_profile("unitree_h1_hardware"))
    snap = scene.ingest({"base": {"x": 0, "y": 0}, "objects": [{"label": "door", "x": 2, "y": 0}]})
    assert snap.calibrated is False
    assert snap.summary()["safe_for_motion_authority"] is False
    assert snap.coverage < 0.5


def test_occupied_cell_blocks_direct_path():
    scene = SceneMap(profile=load_profile("arm_sim"), cell_m=1.0)
    scene.ingest({"objects": [{"label": "wall", "x": 1.0, "y": 0.0}]})
    assert scene.occupancy_at(1.0, 0.0)
    blocked = scene.shortest_path((0.0, 0.0), (1.0, 0.0))
    assert blocked is None
    around = scene.shortest_path((0.0, 0.0), (0.0, 2.0))
    assert around is not None
