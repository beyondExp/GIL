from __future__ import annotations

from gil.learn.curriculum import curriculum_for, curriculum_spec, robot_types, stages_catalog


def test_curriculum_has_all_robot_types() -> None:
    rts = robot_types()
    assert len(rts) >= 3
    assert "humanoid_biped" in rts
    for rt in rts:
        stages = curriculum_for(rt)
        assert stages, f"empty curriculum for {rt}"


def test_stage_ids_are_unique_and_resolvable() -> None:
    cat = stages_catalog()
    assert "safety_do_no_harm" in cat
    assert "instruction_following_natural_env" in cat
    ids = set(cat.keys())
    assert len(ids) == len(cat)


def test_curriculum_spec_is_jsonable_shape() -> None:
    spec = curriculum_spec()
    assert "robot_types" in spec
    assert "stages" in spec
    assert isinstance(spec["robot_types"], list)
    assert isinstance(spec["stages"], dict)
    assert len(spec["robot_types"]) == len(robot_types())

