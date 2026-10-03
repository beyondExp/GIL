from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


TaskKind = Literal["stability", "locomotion", "navigation", "recovery", "coordination"]


@dataclass(frozen=True)
class BodyTask:
    """
    A concrete, executable body-use task for humanoids.

    These tasks are the bridge between:
    - curriculum stages (high-level capability progression), and
    - training data sources (mocap/animations/teleop), and
    - evaluation (pass/fail, stability envelopes).
    """

    id: str
    kind: TaskKind
    title: str
    intent: str
    success_metrics: list[str] = field(default_factory=list)
    hazards: list[str] = field(default_factory=list)
    # Human-readable instructions for running the task in the current repo.
    run: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


def humanoid_body_tasks() -> list[BodyTask]:
    """
    Catalog of "use your own body" tasks for humanoid bipeds.

    Start with tasks that exercise:
    - stability (stand)
    - locomotion primitives (forward, turn pulses)
    - composed navigation patterns (square/circle)

    Later we will attach:
    - mocap / animation demonstrations
    - retargeting to Unitree H1 joint layout
    - imitation learning (BC) + RL fine-tuning
    """
    return [
        BodyTask(
            id="stand_idle_upright",
            kind="stability",
            title="Stand idle upright",
            intent="Hold a stable standing pose without tipping while receiving no motion commands.",
            success_metrics=["base_z_above_threshold_for_duration", "bounded_sway", "no_fall"],
            hazards=["fall"],
            run={"script": "scripts/open_space_stand_stability_test.py"},
            params={"min_z": 0.70, "duration_s": 20.0},
        ),
        BodyTask(
            id="turn_pulses_safe",
            kind="locomotion",
            title="Turn pulses (safe)",
            intent="Execute gentle in-place turns without tipping; establish safe yaw-rate envelope.",
            success_metrics=["dyaw_target", "base_z_above_threshold", "no_fall"],
            hazards=["fall"],
            run={"script": "scripts/mcp_turn_safe_test.py"},
            params={"wz": 0.35, "duration_s": 1.2, "min_z": 0.70},
        ),
        BodyTask(
            id="walk_square_cmdvel_safe",
            kind="coordination",
            title="Walk a square (cmd_vel, safe)",
            intent="Compose forward segments and turn pulses into a square without tipping.",
            success_metrics=["min_base_z_above_threshold", "completes_all_corners", "no_fall"],
            hazards=["fall"],
            run={"script": "scripts/mcp_walk_square_safe.py"},
            params={"vx": 0.08, "forward_s": 2.2, "wz": 0.22, "turn_s": 0.9, "min_z": 0.70},
        ),
        BodyTask(
            id="walk_square_goal_mode",
            kind="navigation",
            title="Walk a square (goal mode)",
            intent="Reach four sequential corner goals relative to current pose (no teleport) while staying upright.",
            success_metrics=["reaches_each_goal_tol", "min_base_z_above_threshold", "no_fall"],
            hazards=["fall"],
            run={"script": "scripts/mcp_walk_square_goal.py"},
            params={"side_m": 1.0, "goal_tol_m": 0.55, "min_z": 0.70, "laps": 1},
        ),
        BodyTask(
            id="ramp_walk_boundary",
            kind="locomotion",
            title="Ramp to tipping boundary",
            intent="Automatically increase vx/wz to find the stability boundary and record the first tip point.",
            success_metrics=["max_stable_vx_wz", "first_tip_point_logged"],
            hazards=["fall"],
            run={"script": "scripts/mcp_ramp_walk.py"},
            params={"min_z": 0.70},
        ),
    ]


def humanoid_body_tasks_spec() -> dict[str, Any]:
    tasks = humanoid_body_tasks()
    return {
        "robot_type": "humanoid_biped",
        "tasks": [
            {
                "id": t.id,
                "kind": t.kind,
                "title": t.title,
                "intent": t.intent,
                "success_metrics": list(t.success_metrics),
                "hazards": list(t.hazards),
                "run": dict(t.run),
                "params": dict(t.params),
            }
            for t in tasks
        ],
    }

