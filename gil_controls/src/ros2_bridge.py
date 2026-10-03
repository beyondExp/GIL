import base64
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


def _optional_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


@dataclass
class Ros2BridgeConfig:
    joint_states_topic: str = "/joint_states"
    ee_pose_topic: str = "/ee_pose"
    odom_topic: str = "/odom"
    imu_topic: str = ""
    battery_state_topic: str = ""
    fault_state_topic: str = ""
    estop_state_topic: str = ""
    robot_mode_state_topic: str = ""
    image_left_topic: str = "/camera/left/image_raw"
    image_right_topic: str = "/camera/right/image_raw"
    image_wide_topic: str = ""
    camera_info_left_topic: str = "/camera/left/camera_info"
    world_frame: str = "world"
    bin_frame: str = "bin"
    cube_red_frame: str = "cube_red"
    cube_green_frame: str = "cube_green"
    cube_blue_frame: str = "cube_blue"
    ee_target_topic: str = "/ee_target"
    gripper_topic: str = "/gripper_open"
    cmd_vel_topic: str = "/cmd_vel"
    walker_mode_topic: str = "/humanoid/mode"
    joint_traj_topic: str = ""
    joint_traj_duration_s: float = 0.25
    reset_seed_topic: str = ""
    jpeg_quality: int = 80
    stale_after_s: float = 1.5


class Ros2Bridge:
    """ROS2 bridge with topic freshness and preflight reporting."""

    def __init__(self, cfg: Ros2BridgeConfig, on_state_update: Callable[[Dict[str, Any]], None]):
        self.cfg = cfg
        self._on_state_update = on_state_update
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._topic_last_seen: dict[str, float] = {}
        self._topic_names: dict[str, str] = {}
        self._last_command_at_s = 0.0
        self._rclpy = None
        self._node = None
        self._pub_ee = None
        self._pub_gripper = None
        self._pub_cmd_vel = None
        self._pub_walker_mode = None
        self._pub_reset = None
        self._pub_joint_traj = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="ros2-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()

    def _mark_seen(self, stream_name: str, topic_name: str) -> None:
        self._topic_last_seen[stream_name] = time.monotonic()
        self._topic_names[stream_name] = topic_name

    def _state_envelope(self, partial: Dict[str, Any], *, source: str) -> Dict[str, Any]:
        env = dict(partial)
        env["_source"] = source
        env["_observed_at_ms"] = int(time.time() * 1000)
        env["_observed_at_monotonic_s"] = time.monotonic()
        return env

    def _emit_state(self, partial: Dict[str, Any], *, source: str) -> None:
        self._on_state_update(self._state_envelope(partial, source=source))

    def _run(self) -> None:
        rclpy = _optional_import("rclpy")
        if rclpy is None:
            raise RuntimeError("ROS2 bridge requested but rclpy is not available in this Python environment.")
        self._rclpy = rclpy

        from geometry_msgs.msg import PoseStamped, Twist
        from nav_msgs.msg import Odometry
        from rclpy.node import Node
        from sensor_msgs.msg import BatteryState, CameraInfo
        from sensor_msgs.msg import Image as RosImage
        from sensor_msgs.msg import Imu, JointState
        from std_msgs.msg import Bool, Int32, String
        from trajectory_msgs.msg import JointTrajectory

        tf2_ros = _optional_import("tf2_ros")

        rclpy.init(args=None)

        class _BridgeNode(Node):
            def __init__(self, outer: "Ros2Bridge"):
                super().__init__("gil_controls_ros2_bridge")
                self.outer = outer
                self.outer._pub_ee = self.create_publisher(PoseStamped, outer.cfg.ee_target_topic, 10)
                self.outer._pub_gripper = self.create_publisher(Bool, outer.cfg.gripper_topic, 10)
                self.outer._pub_cmd_vel = self.create_publisher(Twist, outer.cfg.cmd_vel_topic, 10)
                self.outer._pub_walker_mode = self.create_publisher(String, outer.cfg.walker_mode_topic, 10) if outer.cfg.walker_mode_topic else None
                self.outer._pub_joint_traj = self.create_publisher(JointTrajectory, outer.cfg.joint_traj_topic, 10) if outer.cfg.joint_traj_topic else None
                self.outer._pub_reset = self.create_publisher(Int32, outer.cfg.reset_seed_topic, 10) if outer.cfg.reset_seed_topic else None

                self.tf_buffer = None
                self.tf_listener = None
                if tf2_ros is not None:
                    try:
                        self.tf_buffer = tf2_ros.Buffer()
                        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
                    except Exception:
                        self.tf_buffer = None
                        self.tf_listener = None

                self.create_subscription(JointState, outer.cfg.joint_states_topic, self._on_joint_state, 10)
                self.create_subscription(PoseStamped, outer.cfg.ee_pose_topic, self._on_ee_pose, 10)
                if outer.cfg.odom_topic:
                    self.create_subscription(Odometry, outer.cfg.odom_topic, self._on_odom, 10)
                if outer.cfg.imu_topic:
                    self.create_subscription(Imu, outer.cfg.imu_topic, self._on_imu, 10)
                if outer.cfg.battery_state_topic:
                    self.create_subscription(BatteryState, outer.cfg.battery_state_topic, self._on_battery, 10)
                if outer.cfg.fault_state_topic:
                    self.create_subscription(String, outer.cfg.fault_state_topic, self._on_faults, 10)
                if outer.cfg.estop_state_topic:
                    self.create_subscription(Bool, outer.cfg.estop_state_topic, self._on_estop, 10)
                if outer.cfg.robot_mode_state_topic:
                    self.create_subscription(String, outer.cfg.robot_mode_state_topic, self._on_mode_state, 10)
                if outer.cfg.image_left_topic:
                    self.create_subscription(RosImage, outer.cfg.image_left_topic, self._on_image_left, 2)
                if outer.cfg.image_right_topic:
                    self.create_subscription(RosImage, outer.cfg.image_right_topic, self._on_image_right, 2)
                if outer.cfg.image_wide_topic:
                    self.create_subscription(RosImage, outer.cfg.image_wide_topic, self._on_image_wide, 2)
                if outer.cfg.camera_info_left_topic:
                    self.create_subscription(CameraInfo, outer.cfg.camera_info_left_topic, self._on_cam_info_left, 10)

            @staticmethod
            def _yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
                import math

                siny_cosp = 2.0 * (w * z + x * y)
                cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
                return float(math.atan2(siny_cosp, cosy_cosp))

            def _jpeg_data_url(self, msg: RosImage) -> Optional[str]:
                try:
                    from PIL import Image as PILImage
                    import numpy as np
                    import io

                    w = int(msg.width)
                    h = int(msg.height)
                    enc = str(msg.encoding or "").lower()
                    buf = bytes(msg.data)
                    if enc == "rgb8":
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
                    elif enc == "rgba8":
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
                    elif enc == "bgr8":
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))[:, :, ::-1]
                    else:
                        return None

                    im = PILImage.fromarray(arr, mode="RGB")
                    out = io.BytesIO()
                    quality = int(max(10, min(95, self.outer.cfg.jpeg_quality)))
                    im.save(out, format="JPEG", quality=quality)
                    b64 = base64.b64encode(out.getvalue()).decode("ascii")
                    return f"data:image/jpeg;base64,{b64}"
                except Exception:
                    return None

            def _on_joint_state(self, msg: JointState) -> None:
                try:
                    self.outer._mark_seen("joint_states", self.outer.cfg.joint_states_topic)
                    joints = {str(n): float(p) for n, p in zip(msg.name, msg.position)}
                    partial: Dict[str, Any] = {"joints": joints}
                    if getattr(msg, "velocity", None):
                        partial["joints_vel"] = {str(n): float(v) for n, v in zip(msg.name, msg.velocity)}
                    self.outer._emit_state(partial, source="ros2:joint_states")
                except Exception:
                    return

            def _on_ee_pose(self, msg: PoseStamped) -> None:
                self.outer._mark_seen("ee_pose", self.outer.cfg.ee_pose_topic)
                p = msg.pose.position
                self.outer._emit_state(
                    {"end_effector": {"x": float(p.x), "y": float(p.y), "z": float(p.z)}},
                    source="ros2:ee_pose",
                )
                if self.tf_buffer is not None:
                    objs = {"bin": None, "cubes": []}
                    for name, frame in [
                        ("bin", self.outer.cfg.bin_frame),
                        ("cube_red", self.outer.cfg.cube_red_frame),
                        ("cube_green", self.outer.cfg.cube_green_frame),
                        ("cube_blue", self.outer.cfg.cube_blue_frame),
                    ]:
                        if not frame:
                            continue
                        try:
                            tf = self.tf_buffer.lookup_transform(self.outer.cfg.world_frame, frame, rclpy.time.Time())
                            tp = tf.transform.translation
                            if name == "bin":
                                objs["bin"] = {"x": float(tp.x), "y": float(tp.y), "z": float(tp.z)}
                            else:
                                objs["cubes"].append(
                                    {"name": name, "x": float(tp.x), "y": float(tp.y), "z": float(tp.z), "held": False}
                                )
                        except Exception:
                            continue
                    if objs["bin"] or objs["cubes"]:
                        self.outer._emit_state({"objects": objs}, source="ros2:tf")

            def _on_image_left(self, msg: RosImage) -> None:
                self.outer._mark_seen("image_left", self.outer.cfg.image_left_topic)
                image = self._jpeg_data_url(msg)
                if image:
                    self.outer._emit_state({"last_image_left": image, "last_image": image}, source="ros2:image_left")

            def _on_image_right(self, msg: RosImage) -> None:
                self.outer._mark_seen("image_right", self.outer.cfg.image_right_topic)
                image = self._jpeg_data_url(msg)
                if image:
                    self.outer._emit_state({"last_image_right": image}, source="ros2:image_right")

            def _on_image_wide(self, msg: RosImage) -> None:
                self.outer._mark_seen("image_wide", self.outer.cfg.image_wide_topic)
                image = self._jpeg_data_url(msg)
                if image:
                    self.outer._emit_state({"last_image_wide": image}, source="ros2:image_wide")

            def _on_cam_info_left(self, msg: CameraInfo) -> None:
                self.outer._mark_seen("camera_info", self.outer.cfg.camera_info_left_topic)
                payload = {
                    "frame_id": str(msg.header.frame_id),
                    "width": int(msg.width),
                    "height": int(msg.height),
                    "k": list(msg.k),
                }
                self.outer._emit_state({"camera_info": payload}, source="ros2:camera_info")

            def _on_odom(self, msg: Odometry) -> None:
                try:
                    self.outer._mark_seen("odom", self.outer.cfg.odom_topic)
                    p = msg.pose.pose.position
                    q = msg.pose.pose.orientation
                    yaw = self._yaw_from_quat(float(q.x), float(q.y), float(q.z), float(q.w))
                    t = msg.twist.twist
                    base = {
                        "x": float(p.x),
                        "y": float(p.y),
                        "z": float(p.z),
                        "yaw": float(yaw),
                        "vx": float(t.linear.x),
                        "vy": float(t.linear.y),
                        "wz": float(t.angular.z),
                    }
                    self.outer._emit_state({"base": base}, source="ros2:odom")
                except Exception:
                    return

            def _on_imu(self, msg: Imu) -> None:
                try:
                    self.outer._mark_seen("imu", self.outer.cfg.imu_topic)
                    q = msg.orientation
                    yaw = self._yaw_from_quat(float(q.x), float(q.y), float(q.z), float(q.w))
                    imu = {
                        "yaw": float(yaw),
                        "wx": float(msg.angular_velocity.x),
                        "wy": float(msg.angular_velocity.y),
                        "wz": float(msg.angular_velocity.z),
                        "ax": float(msg.linear_acceleration.x),
                        "ay": float(msg.linear_acceleration.y),
                        "az": float(msg.linear_acceleration.z),
                    }
                    self.outer._emit_state({"imu": imu, "sensors": {"imu": imu}}, source="ros2:imu")
                except Exception:
                    return

            def _on_battery(self, msg: BatteryState) -> None:
                self.outer._mark_seen("battery", self.outer.cfg.battery_state_topic)
                battery = {
                    "percentage": float(msg.percentage),
                    "voltage": float(msg.voltage),
                    "current": float(msg.current),
                    "power_supply_status": int(msg.power_supply_status),
                }
                self.outer._emit_state({"battery": battery}, source="ros2:battery")

            def _on_faults(self, msg: String) -> None:
                self.outer._mark_seen("faults", self.outer.cfg.fault_state_topic)
                faults = [line.strip() for line in str(msg.data or "").split(";") if line.strip()]
                self.outer._emit_state({"faults": faults}, source="ros2:faults")

            def _on_estop(self, msg: Bool) -> None:
                self.outer._mark_seen("estop", self.outer.cfg.estop_state_topic)
                self.outer._emit_state({"safety": {"estop": bool(msg.data)}}, source="ros2:estop")

            def _on_mode_state(self, msg: String) -> None:
                self.outer._mark_seen("mode", self.outer.cfg.robot_mode_state_topic)
                self.outer._emit_state({"mode": str(msg.data or "").strip()}, source="ros2:mode")

        self._node = _BridgeNode(self)

        try:
            while rclpy.ok() and (not self._stop_evt.is_set()):
                rclpy.spin_once(self._node, timeout_sec=0.1)
        finally:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            try:
                rclpy.shutdown()
            except Exception:
                pass

    def get_health(self) -> Dict[str, Any]:
        now_mono = time.monotonic()
        topics = {}
        for stream, seen_at in self._topic_last_seen.items():
            topics[stream] = {
                "topic": self._topic_names.get(stream, ""),
                "age_s": max(0.0, now_mono - seen_at),
                "stale": (now_mono - seen_at) > self.cfg.stale_after_s,
            }
        return {
            "running": self._node is not None,
            "stale_after_s": self.cfg.stale_after_s,
            "topics": topics,
            "last_command_age_s": max(0.0, now_mono - self._last_command_at_s) if self._last_command_at_s else None,
        }

    def preflight_check(self, mode: str = "sim_ros2") -> Dict[str, Any]:
        health = self.get_health()
        required = ["odom"]
        if mode == "h1_hardware":
            required.append("joint_states")
            if self.cfg.imu_topic:
                required.append("imu")
            if self.cfg.battery_state_topic:
                required.append("battery")
        missing = [name for name in required if name not in health["topics"]]
        stale = [name for name in required if health["topics"].get(name, {}).get("stale")]
        ok = bool(health.get("running")) and (not missing) and (not stale)
        return {
            "ok": ok,
            "backend": mode,
            "required_topics": required,
            "missing_topics": missing,
            "stale_topics": stale,
            "health": health,
        }

    def send_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        if self._node is None:
            return {"success": False, "error": "ROS2 node not running"}

        from geometry_msgs.msg import PoseStamped, Twist
        from std_msgs.msg import Bool, Int32, String
        from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

        self._last_command_at_s = time.monotonic()
        command_type = str(cmd.get("type") or "")
        if command_type == "move_robot":
            msg = PoseStamped()
            msg.header.frame_id = self.cfg.world_frame
            msg.header.stamp = self._node.get_clock().now().to_msg()
            msg.pose.position.x = float(cmd.get("x", 0.0))
            msg.pose.position.y = float(cmd.get("y", 0.0))
            msg.pose.position.z = float(cmd.get("z", 0.0))
            msg.pose.orientation.w = 1.0
            self._pub_ee.publish(msg)
            return {"success": True, "published_topic": self.cfg.ee_target_topic}
        if command_type == "gripper":
            msg = Bool()
            msg.data = bool(cmd.get("open", True))
            self._pub_gripper.publish(msg)
            return {"success": True, "published_topic": self.cfg.gripper_topic}
        if command_type == "cmd_vel":
            msg = Twist()
            msg.linear.x = float(cmd.get("vx", 0.0))
            msg.linear.y = float(cmd.get("vy", 0.0))
            msg.angular.z = float(cmd.get("wz", 0.0))
            self._pub_cmd_vel.publish(msg)
            return {"success": True, "published_topic": self.cfg.cmd_vel_topic}
        if command_type == "walker_mode":
            if self._pub_walker_mode is None:
                return {"success": False, "error": "walker_mode_topic not configured for ROS2 bridge"}
            mode = str(cmd.get("mode") or "").strip().lower()
            if mode not in ("external", "goal"):
                return {"success": False, "error": "walker_mode must be 'external' or 'goal'"}
            msg = String()
            msg.data = mode
            self._pub_walker_mode.publish(msg)
            return {"success": True, "published_topic": self.cfg.walker_mode_topic, "mode": mode}
        if command_type in ("set_joint_targets", "joint_targets", "set_angles"):
            if self._pub_joint_traj is None:
                return {"success": False, "error": "joint_traj_topic not configured for ROS2 bridge"}
            angles = cmd.get("angles") if isinstance(cmd.get("angles"), dict) else cmd.get("joints")
            if not isinstance(angles, dict):
                return {"success": False, "error": "angles must be dict mapping joint_name->position"}
            jt = JointTrajectory()
            jt.joint_names = [str(k) for k in angles.keys()]
            pt = JointTrajectoryPoint()
            pt.positions = [float(angles[k]) for k in angles.keys()]
            dur = float(getattr(self.cfg, "joint_traj_duration_s", 0.25))
            dur = max(0.02, min(2.0, dur))
            pt.time_from_start.sec = int(dur)
            pt.time_from_start.nanosec = int((dur - int(dur)) * 1e9)
            jt.points = [pt]
            self._pub_joint_traj.publish(jt)
            return {"success": True, "published_topic": self.cfg.joint_traj_topic}
        if command_type in ("reset_scene", "reset_episode") and self._pub_reset is not None:
            msg = Int32()
            msg.data = int(cmd.get("seed", 0))
            self._pub_reset.publish(msg)
            return {"success": True, "published_topic": self.cfg.reset_seed_topic}
        return {"success": False, "error": f"Unsupported command type: {command_type}"}


