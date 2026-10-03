from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ActionSpace(BaseModel):
    type: Literal["cmd_vel", "ee_delta", "joint_traj"]
    dims: list[str]
    max_abs: dict[str, float] = Field(default_factory=dict)


class CameraSpec(BaseModel):
    name: str
    frame: str = "camera_link"
    topic: str = ""
    info_topic: str = ""
    calibrated: bool = False
    fx: float | None = None
    fy: float | None = None
    cx: float | None = None
    cy: float | None = None


class SafetySpec(BaseModel):
    stale_state_after_s: float = 1.5
    heartbeat_timeout_s: float = 2.0
    max_vx: float = 0.6
    max_vy: float = 0.4
    max_wz: float = 1.0
    max_drive_duration_s: float = 10.0
    limp_vx: float = 0.05
    require_motion_enable: bool = True
    require_estop_topic: bool = False
    require_camera_info: bool = False


class TopicMap(BaseModel):
    cmd_vel: str = "/cmd_vel"
    odom: str = "/odom"
    joint_states: str = "/joint_states"
    imu: str = ""
    battery: str = ""
    fault: str = ""
    estop: str = ""
    mode_state: str = ""
    image_left: str = ""
    image_right: str = ""
    camera_info_left: str = ""


class RobotProfile(BaseModel):
    profile_id: str
    display_name: str
    kind: Literal["arm", "humanoid", "mobile", "other"] = "humanoid"
    backend: Literal["sim_ws", "sim_ros2", "h1_hardware", "mock"] = "sim_ws"
    action_space: ActionSpace
    cameras: list[CameraSpec] = Field(default_factory=list)
    safety: SafetySpec = Field(default_factory=SafetySpec)
    topics: TopicMap = Field(default_factory=TopicMap)
    frames: dict[str, str] = Field(default_factory=lambda: {"world": "world", "base": "base_link"})
    schema_version: int = 1

    @field_validator("profile_id")
    @classmethod
    def _id_ok(cls, value: str) -> str:
        if not value or "/" in value or " " in value:
            raise ValueError("profile_id must be a non-empty token without spaces")
        return value

    @property
    def is_hardware(self) -> bool:
        return self.backend in {"h1_hardware"}

    @property
    def cameras_calibrated(self) -> bool:
        cams = [c for c in self.cameras if c.name]
        return bool(cams) and all(c.calibrated for c in cams)

    def camera_by_name(self, name: str) -> CameraSpec | None:
        for cam in self.cameras:
            if cam.name == name:
                return cam
        return None


def profile_from_legacy(raw: dict[str, Any]) -> RobotProfile:
    """Accept hardware/h1/default_profile.json and normalize it."""
    if "profile_id" in raw and "action_space" in raw:
        return RobotProfile.model_validate(raw)

    topics = raw.get("topics") or {}
    safety = raw.get("safety") or {}
    backend = str(raw.get("backend_mode") or raw.get("backend") or "sim_ws")
    hardware = backend == "h1_hardware"
    return RobotProfile(
        profile_id=str(raw.get("profile_name") or "legacy_robot"),
        display_name=str(raw.get("profile_name") or "Legacy robot"),
        kind="humanoid",
        backend=backend if backend in {"sim_ws", "sim_ros2", "h1_hardware", "mock"} else "sim_ws",
        action_space=ActionSpace(type="cmd_vel", dims=["vx", "vy", "wz"], max_abs={"vx": float(safety.get("max_vx", 0.6))}),
        cameras=[
            CameraSpec(
                name="left",
                topic=str(topics.get("image_left") or ""),
                info_topic=str(topics.get("camera_info_left") or ""),
                calibrated=False,
            )
        ],
        safety=SafetySpec(
            stale_state_after_s=float(safety.get("stale_state_after_s", 1.5)),
            heartbeat_timeout_s=float(safety.get("heartbeat_timeout_s", 2.0)),
            max_vx=float(safety.get("max_vx", 0.6)),
            max_vy=float(safety.get("max_vy", 0.4)),
            max_wz=float(safety.get("max_wz", 0.6)),
            require_estop_topic=hardware,
            require_camera_info=hardware,
        ),
        topics=TopicMap(
            cmd_vel=str(topics.get("cmd_vel") or "/cmd_vel"),
            odom=str(topics.get("odom") or "/odom"),
            joint_states=str(topics.get("joint_states") or "/joint_states"),
            imu=str(topics.get("imu") or ""),
            battery=str(topics.get("battery") or ""),
            fault=str(topics.get("fault") or ""),
            estop=str(topics.get("estop") or ""),
            mode_state=str(topics.get("mode_state") or ""),
            image_left=str(topics.get("image_left") or ""),
            image_right=str(topics.get("image_right") or ""),
            camera_info_left=str(topics.get("camera_info_left") or ""),
        ),
    )


class Role(str, Enum):
    observer = "observer"
    operator = "operator"
    autonomy = "autonomy"


class GilSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GIL_", extra="ignore")

    humanoid_backend: str = "sim_ws"
    use_ros2: bool = False
    require_motion_enable: bool = True
    state_stale_after_s: float = 1.5
    heartbeat_timeout_s: float = 2.0
    max_vx: float = 0.6
    max_vy: float = 0.4
    max_wz: float = 1.0
    operator_token: str = ""
    autonomy_token: str = ""
    log_level: str = "INFO"
    allow_estimated_object_coordinates: bool = False
    default_profile: str = "arm_sim"

    # --- Memory (optional) ---
    # Neo4j is used as a long-horizon spatial/semantic memory (scene graph).
    neo4j_enabled: bool = False
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
