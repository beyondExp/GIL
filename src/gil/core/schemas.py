from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    name: str
    description: str
    required: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    version: str = "1"


ORCHESTRATOR_TOOLS = [
    ToolSchema(
        name="connect_robot",
        description="Attach a robot profile to this session.",
        required=["profile_id"],
        properties={"profile_id": {"type": "string"}, "robot_id": {"type": "string"}, "token": {"type": "string"}},
    ),
    ToolSchema(
        name="get_situation",
        description="Return state, map summary, health, and gate status for a robot.",
        required=["robot_id"],
        properties={"robot_id": {"type": "string"}},
    ),
    ToolSchema(
        name="set_goal",
        description="Set the mission goal in world frame or as language.",
        required=["robot_id", "goal"],
        properties={"robot_id": {"type": "string"}, "goal": {"type": "object"}},
    ),
    ToolSchema(
        name="run_mission",
        description="Perceive, dream, gate, then execute only if the confidence gate passes.",
        required=["robot_id"],
        properties={"robot_id": {"type": "string"}, "n_dreams": {"type": "integer"}, "token": {"type": "string"}},
    ),
    ToolSchema(
        name="abort",
        description="Disable motion and stop the robot.",
        required=["robot_id"],
        properties={"robot_id": {"type": "string"}},
    ),
    ToolSchema(
        name="list_embodiments",
        description="List Isaac Sim robot bodies the agent may select.",
        properties={},
    ),
    ToolSchema(
        name="select_embodiment",
        description="Attach an Isaac robot (H1, G1, Franka, ...).",
        required=["key"],
        properties={"key": {"type": "string"}, "robot_id": {"type": "string"}, "token": {"type": "string"}},
    ),
    ToolSchema(
        name="ingest_world",
        description="Build or record a world from text, image, video, or huggingface occupancy JSON. Live today: Isaac maze or HF occupancy grid.",
        required=["source", "content"],
        properties={"source": {"type": "string"}, "content": {"type": "string"}},
    ),
    ToolSchema(
        name="instruct",
        description="Tell the robot what it must be able to do.",
        required=["instruction"],
        properties={"instruction": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}},
    ),
    ToolSchema(
        name="dream",
        description="Imagine rollouts in a copy of the Isaac scene. Does not execute.",
        properties={"n": {"type": "integer"}},
    ),
    ToolSchema(
        name="preview_plan",
        description="Return preview_vel commands to watch the kept dream in Isaac.",
        properties={},
    ),
    ToolSchema(
        name="steer",
        description="Agent process: pick body, ingest world, instruct, dream, optionally commit if the gate passes.",
        properties={
            "instruction": {"type": "string"},
            "embodiment": {"type": "string"},
            "world_source": {"type": "string"},
            "world_content": {"type": "string"},
            "commit": {"type": "boolean"},
        },
    ),
]
