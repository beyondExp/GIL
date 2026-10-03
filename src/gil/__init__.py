"""GIL production core: profiles, safety authority, orchestrator, world, learn."""

__version__ = "0.1.0"

PRODUCT = "General Intelligence Layer"
MOTOR_COMMAND_TYPES = frozenset(
    {"cmd_vel", "preview_vel", "move_robot", "gripper", "joint_traj", "set_joint_targets", "set_angles"}
)
UNTRUSTED_MOTION_SOURCES = frozenset(
    {"world_model", "dream", "vlm", "critic", "gemini", "groot", "cosmos"}
)
