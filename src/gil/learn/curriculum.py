from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# Broad product-facing categories (what capabilities/risks to expect).
RobotType = Literal[
    "humanoid_biped",
    "quadruped",
    "wheeled_base",
    "tracked_base",
    "manipulator_arm",
    "mobile_manipulator",
    "aerial_drone",
]


@dataclass(frozen=True)
class LearningStage:
    """
    A curriculum stage the system can train/evaluate in dreaming and (optionally) execute.

    This is intentionally implementation-agnostic: later we can bind stages to
    critics, gates, reward shaping, datasets, or Isaac tasks.
    """

    id: str
    title: str
    intent: str
    # What needs to be true before we attempt this stage.
    prerequisites: list[str] = field(default_factory=list)
    # Canonical success checks (names are stable; implementations can vary per robot/env).
    success_metrics: list[str] = field(default_factory=list)
    # The primary hazard to guard against (used to pick critics + gates).
    primary_risk: str = ""
    # Optional stage params (e.g. speed limits, terrain classes).
    params: dict[str, Any] = field(default_factory=dict)


def robot_types() -> list[RobotType]:
    # Keep deterministic ordering.
    return [
        "humanoid_biped",
        "quadruped",
        "wheeled_base",
        "tracked_base",
        "manipulator_arm",
        "mobile_manipulator",
        "aerial_drone",
    ]


def stages_catalog() -> dict[str, LearningStage]:
    """
    Global stage definitions.

    Stages are composable: each robot type selects a subset and may override params.
    """
    S = LearningStage
    return {
        # Cross-cutting: safety + self-preservation.
        "safety_do_no_harm": S(
            id="safety_do_no_harm",
            title="Do no harm (self + environment)",
            intent="Learn hard safety constraints first: stop on hazard, never exceed safe envelopes.",
            prerequisites=[],
            success_metrics=["no_self_collision", "no_ground_penetration", "bounded_energy", "obeys_estop"],
            primary_risk="injury_or_damage",
            params={"speed_cap": 0.1},
        ),
        "proprioception_calibration": S(
            id="proprioception_calibration",
            title="Body sensing + calibration",
            intent="Stabilize state estimation signals used by dreaming (pose, contacts, IMU).",
            prerequisites=["safety_do_no_harm"],
            success_metrics=["stable_base_pose", "stable_contact_flags", "no_nan_state"],
            primary_risk="unstable_state_estimation",
        ),
        # Humanoid / legged core.
        "stand_idle": S(
            id="stand_idle",
            title="Stand idle",
            intent="Maintain a neutral standing pose without drifting or collapsing.",
            prerequisites=["proprioception_calibration"],
            success_metrics=["upright_for_20s", "bounded_sway", "no_fall"],
            primary_risk="fall",
        ),
        "balance_recovery": S(
            id="balance_recovery",
            title="Balance recovery",
            intent="Recover from gentle pushes/impulses without stepping into unsafe states.",
            prerequisites=["stand_idle"],
            success_metrics=["recovers_after_disturbance", "no_fall", "no_foot_slip"],
            primary_risk="fall",
            params={"disturbance_levels": ["light", "medium"]},
        ),
        "stand_up_from_fall": S(
            id="stand_up_from_fall",
            title="Stand up (self-recovery)",
            intent="Detect fallen states and execute a safe get-up routine.",
            prerequisites=["safety_do_no_harm", "proprioception_calibration"],
            success_metrics=["detects_fall", "returns_to_stand", "no_joint_limit_violations"],
            primary_risk="self_damage_on_getup",
        ),
        "locomotion_forward": S(
            id="locomotion_forward",
            title="Walk forward",
            intent="Learn stable forward stepping at low speed on flat ground.",
            prerequisites=["stand_idle"],
            success_metrics=["walks_1m_no_fall", "bounded_slip", "bounded_yaw_drift"],
            primary_risk="fall",
            params={"vx_cap": 0.15},
        ),
        "locomotion_turning": S(
            id="locomotion_turning",
            title="Turning without tipping",
            intent="Learn turn-in-place and gentle arcing turns without entering the tipping regime.",
            prerequisites=["locomotion_forward"],
            success_metrics=["turn_90deg_no_fall", "turn_pulses_stable"],
            primary_risk="fall",
            params={"wz_cap": 0.3, "prefer_pulses": True},
        ),
        "stop_and_hold": S(
            id="stop_and_hold",
            title="Stop and hold",
            intent="Stop quickly and maintain balance (no post-stop wobble/fall).",
            prerequisites=["locomotion_forward"],
            success_metrics=["stops_within_0p3m", "holds_upright_10s"],
            primary_risk="fall",
        ),
        # Navigation.
        "collision_avoidance": S(
            id="collision_avoidance",
            title="Collision avoidance",
            intent="Avoid obstacles using local sensing + conservative action gating.",
            prerequisites=["locomotion_forward"],
            success_metrics=["no_wall_contacts", "no_unsafe_close_passes"],
            primary_risk="collision",
        ),
        "local_goal_reaching": S(
            id="local_goal_reaching",
            title="Local goal reaching",
            intent="Reach a nearby goal (1–3m) in open space with safe turning.",
            prerequisites=["locomotion_turning", "stop_and_hold"],
            success_metrics=["reaches_goal_radius", "no_fall", "bounded_time"],
            primary_risk="fall",
            params={"goal_radius": 0.75},
        ),
        "maze_escape": S(
            id="maze_escape",
            title="Maze escape",
            intent="Goal reaching with tight constraints; requires precise turning + collision avoidance.",
            prerequisites=["local_goal_reaching", "collision_avoidance"],
            success_metrics=["reaches_exit", "no_wall_contacts", "no_fall"],
            primary_risk="fall_or_collision",
        ),
        # Manipulation.
        "reach": S(
            id="reach",
            title="Reach targets safely",
            intent="Move end-effector to targets without collisions or joint stress.",
            prerequisites=["safety_do_no_harm", "proprioception_calibration"],
            success_metrics=["reaches_target", "no_self_collision", "bounded_joint_torque"],
            primary_risk="collision",
        ),
        "grasp": S(
            id="grasp",
            title="Grasp",
            intent="Acquire stable grasps across simple object set.",
            prerequisites=["reach"],
            success_metrics=["stable_grasp_2s", "no_drop_on_lift"],
            primary_risk="object_drop",
        ),
        "pick_place": S(
            id="pick_place",
            title="Pick and place",
            intent="Pick objects and place into target region/bin reliably.",
            prerequisites=["grasp"],
            success_metrics=["place_success", "no_drop_outside_bin"],
            primary_risk="object_drop",
        ),
        # Instruction following (final product).
        "language_grounding": S(
            id="language_grounding",
            title="Language grounding",
            intent="Map instructions to goals, constraints, and termination conditions.",
            prerequisites=["safety_do_no_harm"],
            success_metrics=["parses_goal", "asks_for_clarification_when_unsafe"],
            primary_risk="unsafe_interpretation",
        ),
        "instruction_following_skills": S(
            id="instruction_following_skills",
            title="Instruction following (skills)",
            intent="Follow single-skill instructions (go-to, turn, stop, pick, place) with guardrails.",
            prerequisites=["language_grounding"],
            success_metrics=["executes_skill_correctly", "no_safety_violation"],
            primary_risk="unsafe_execution",
        ),
        "instruction_following_natural_env": S(
            id="instruction_following_natural_env",
            title="Instruction following (natural environment)",
            intent="Follow multi-step instructions in a natural environment with perception + recovery.",
            prerequisites=["instruction_following_skills", "local_goal_reaching"],
            success_metrics=["multi_step_completion", "recovers_from_minor_failures", "no_safety_violation"],
            primary_risk="compounding_errors",
        ),
    }


def curriculum_for(robot_type: RobotType) -> list[LearningStage]:
    cat = stages_catalog()

    def pick(ids: list[str]) -> list[LearningStage]:
        return [cat[i] for i in ids]

    # Staged progression per robot type.
    if robot_type == "humanoid_biped":
        return pick(
            [
                "safety_do_no_harm",
                "proprioception_calibration",
                "stand_idle",
                "balance_recovery",
                "stand_up_from_fall",
                "locomotion_forward",
                "stop_and_hold",
                "locomotion_turning",
                "collision_avoidance",
                "local_goal_reaching",
                "maze_escape",
                "language_grounding",
                "instruction_following_skills",
                "instruction_following_natural_env",
            ]
        )

    if robot_type == "quadruped":
        return pick(
            [
                "safety_do_no_harm",
                "proprioception_calibration",
                "stand_idle",
                "balance_recovery",
                "stand_up_from_fall",
                "locomotion_forward",
                "stop_and_hold",
                "locomotion_turning",
                "collision_avoidance",
                "local_goal_reaching",
                "maze_escape",
                "language_grounding",
                "instruction_following_skills",
                "instruction_following_natural_env",
            ]
        )

    if robot_type in ("wheeled_base", "tracked_base"):
        return pick(
            [
                "safety_do_no_harm",
                "proprioception_calibration",
                "collision_avoidance",
                "local_goal_reaching",
                "maze_escape",
                "language_grounding",
                "instruction_following_skills",
                "instruction_following_natural_env",
            ]
        )

    if robot_type == "manipulator_arm":
        return pick(
            [
                "safety_do_no_harm",
                "proprioception_calibration",
                "reach",
                "grasp",
                "pick_place",
                "language_grounding",
                "instruction_following_skills",
                "instruction_following_natural_env",
            ]
        )

    if robot_type == "mobile_manipulator":
        return pick(
            [
                "safety_do_no_harm",
                "proprioception_calibration",
                "collision_avoidance",
                "local_goal_reaching",
                "reach",
                "grasp",
                "pick_place",
                "language_grounding",
                "instruction_following_skills",
                "instruction_following_natural_env",
            ]
        )

    if robot_type == "aerial_drone":
        # Placeholder: drone-specific stages can be expanded later (takeoff/landing, failsafe, etc.).
        return pick(
            [
                "safety_do_no_harm",
                "proprioception_calibration",
                "collision_avoidance",
                "local_goal_reaching",
                "language_grounding",
                "instruction_following_skills",
                "instruction_following_natural_env",
            ]
        )

    raise ValueError(f"Unknown robot_type: {robot_type!r}")


def curriculum_spec() -> dict[str, Any]:
    """
    JSON-serializable description (for CLI/MCP/UI).
    """
    out: dict[str, Any] = {"robot_types": [], "stages": {}}
    cat = stages_catalog()
    for rt in robot_types():
        stages = curriculum_for(rt)
        out["robot_types"].append(
            {
                "robot_type": rt,
                "stages": [s.id for s in stages],
            }
        )
    out["stages"] = {
        sid: {
            "id": s.id,
            "title": s.title,
            "intent": s.intent,
            "prerequisites": list(s.prerequisites),
            "success_metrics": list(s.success_metrics),
            "primary_risk": s.primary_risk,
            "params": dict(s.params),
        }
        for sid, s in cat.items()
    }
    return out

