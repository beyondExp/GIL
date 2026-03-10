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
    # Subscriptions
    joint_states_topic: str = "/joint_states"
    ee_pose_topic: str = "/ee_pose"
    odom_topic: str = "/odom"  # nav_msgs/Odometry (base pose + twist)
    imu_topic: str = ""  # sensor_msgs/Imu (optional)
    image_left_topic: str = "/camera/left/image_raw"
    image_right_topic: str = "/camera/right/image_raw"
    image_wide_topic: str = ""  # optional
    camera_info_left_topic: str = "/camera/left/camera_info"

    # TF frames (optional; used for object/bin pose if available)
    world_frame: str = "world"
    bin_frame: str = "bin"
    cube_red_frame: str = "cube_red"
    cube_green_frame: str = "cube_green"
    cube_blue_frame: str = "cube_blue"

    # Publications
    ee_target_topic: str = "/ee_target"
    gripper_topic: str = "/gripper_open"
    cmd_vel_topic: str = "/cmd_vel"
    walker_mode_topic: str = "/humanoid/mode"  # std_msgs/String (e.g. "external" | "goal")
    joint_traj_topic: str = ""  # trajectory_msgs/JointTrajectory (optional)
    joint_traj_duration_s: float = 0.25
    reset_seed_topic: str = ""  # optional, Int32 seed

    # Image encoding
    jpeg_quality: int = 80


class Ros2Bridge:
    """ROS2 bridge for Isaac Sim.

    - Subscribes to state topics and pushes updates to the provided callback.
    - Publishes command topics from send_command().

    This module is intentionally optional: it only works when ROS2 (rclpy) is available.
    """

    def __init__(self, cfg: Ros2BridgeConfig, on_state_update: Callable[[Dict[str, Any]], None]):
        self.cfg = cfg
        self._on_state_update = on_state_update
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        self._rclpy = None
        self._node = None
        self._pub_ee = None
        self._pub_gripper = None
        self._pub_cmd_vel = None
        self._pub_walker_mode = None
        self._pub_reset = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="ros2-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()

    def _run(self) -> None:
        rclpy = _optional_import("rclpy")
        if rclpy is None:
            raise RuntimeError("ROS2 bridge requested but rclpy is not available in this Python environment.")
        self._rclpy = rclpy

        from rclpy.node import Node
        from sensor_msgs.msg import Image as RosImage
        from sensor_msgs.msg import CameraInfo
        from sensor_msgs.msg import JointState
        from sensor_msgs.msg import Imu
        from geometry_msgs.msg import PoseStamped, Twist
        from nav_msgs.msg import Odometry
        from std_msgs.msg import Bool, Int32, String
        from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

        # Optional TF
        tf2_ros = _optional_import("tf2_ros")

        rclpy.init(args=None)

        class _BridgeNode(Node):
            def __init__(self, outer: "Ros2Bridge"):
                super().__init__("gil_controls_ros2_bridge")
                self.outer = outer

                # publishers
                self.outer._pub_ee = self.create_publisher(PoseStamped, outer.cfg.ee_target_topic, 10)
                self.outer._pub_gripper = self.create_publisher(Bool, outer.cfg.gripper_topic, 10)
                self.outer._pub_cmd_vel = self.create_publisher(Twist, outer.cfg.cmd_vel_topic, 10)
                if outer.cfg.walker_mode_topic:
                    self.outer._pub_walker_mode = self.create_publisher(String, outer.cfg.walker_mode_topic, 10)
                else:
                    self.outer._pub_walker_mode = None
                self.outer._pub_joint_traj = None
                if outer.cfg.joint_traj_topic:
                    self.outer._pub_joint_traj = self.create_publisher(JointTrajectory, outer.cfg.joint_traj_topic, 10)
                if outer.cfg.reset_seed_topic:
                    self.outer._pub_reset = self.create_publisher(Int32, outer.cfg.reset_seed_topic, 10)

                # tf listener
                self.tf_buffer = None
                self.tf_listener = None
                if tf2_ros is not None:
                    try:
                        self.tf_buffer = tf2_ros.Buffer()
                        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
                    except Exception:
                        self.tf_buffer = None
                        self.tf_listener = None

                # subscriptions
                self.create_subscription(JointState, outer.cfg.joint_states_topic, self._on_joint_state, 10)
                self.create_subscription(PoseStamped, outer.cfg.ee_pose_topic, self._on_ee_pose, 10)
                if outer.cfg.odom_topic:
                    self.create_subscription(Odometry, outer.cfg.odom_topic, self._on_odom, 10)
                if outer.cfg.imu_topic:
                    self.create_subscription(Imu, outer.cfg.imu_topic, self._on_imu, 10)
                self.create_subscription(RosImage, outer.cfg.image_left_topic, self._on_image_left, 2)
                self.create_subscription(RosImage, outer.cfg.image_right_topic, self._on_image_right, 2)
                if outer.cfg.image_wide_topic:
                    self.create_subscription(RosImage, outer.cfg.image_wide_topic, self._on_image_wide, 2)
                self.create_subscription(CameraInfo, outer.cfg.camera_info_left_topic, self._on_cam_info_left, 10)

                self._last_left = None
                self._last_right = None
                self._last_wide = None
                self._last_cam_info = None
                self._last_imu = None

            @staticmethod
            def _yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
                # Yaw from quaternion (assuming Z-up / ROS REP-103 typical).
                # yaw = atan2(2(wz + xy), 1 - 2(y^2 + z^2))
                import math

                siny_cosp = 2.0 * (w * z + x * y)
                cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
                return float(math.atan2(siny_cosp, cosy_cosp))

            def _jpeg_data_url(self, msg: RosImage) -> Optional[str]:
                try:
                    # msg.data is bytes-like; use PIL to encode jpeg
                    from PIL import Image as PILImage
                    import numpy as np

                    w = int(msg.width)
                    h = int(msg.height)
                    enc = str(msg.encoding or "").lower()
                    buf = bytes(msg.data)

                    if enc in ("rgb8",):
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
                    elif enc in ("rgba8",):
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
                    elif enc in ("bgr8",):
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))[:, :, ::-1]
                    else:
                        # Unknown encoding; give up
                        return None

                    im = PILImage.fromarray(arr, mode="RGB")
                    import io

                    out = io.BytesIO()
                    q = int(max(10, min(95, self.outer.cfg.jpeg_quality)))
                    im.save(out, format="JPEG", quality=q)
                    b64 = base64.b64encode(out.getvalue()).decode("ascii")
                    return f"data:image/jpeg;base64,{b64}"
                except Exception:
                    return None

            def _emit_state(self, partial: Dict[str, Any]) -> None:
                self.outer._on_state_update(partial)

            def _on_joint_state(self, msg: JointState) -> None:
                try:
                    joints = {str(n): float(p) for n, p in zip(msg.name, msg.position)}
                    partial: Dict[str, Any] = {"joints": joints}
                    if getattr(msg, "velocity", None):
                        try:
                            partial["joints_vel"] = {str(n): float(v) for n, v in zip(msg.name, msg.velocity)}
                        except Exception:
                            pass
                    self._emit_state(partial)
                except Exception:
                    return

            def _on_ee_pose(self, msg: PoseStamped) -> None:
                p = msg.pose.position
                self._emit_state({"end_effector": {"x": float(p.x), "y": float(p.y), "z": float(p.z)}})

                # Try TF object poses (optional)
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
                                objs["cubes"].append({"name": name, "x": float(tp.x), "y": float(tp.y), "z": float(tp.z), "held": False})
                        except Exception:
                            continue
                    if objs["bin"] or objs["cubes"]:
                        self._emit_state({"objects": objs})

            def _on_image_left(self, msg: RosImage) -> None:
                self._last_left = self._jpeg_data_url(msg)
                if self._last_left:
                    self._emit_state({"last_image_left": self._last_left, "last_image": self._last_left})

            def _on_image_right(self, msg: RosImage) -> None:
                self._last_right = self._jpeg_data_url(msg)
                if self._last_right:
                    self._emit_state({"last_image_right": self._last_right})

            def _on_image_wide(self, msg: RosImage) -> None:
                self._last_wide = self._jpeg_data_url(msg)
                if self._last_wide:
                    self._emit_state({"last_image_wide": self._last_wide})

            def _on_cam_info_left(self, msg: CameraInfo) -> None:
                # We store camera intrinsics; pose is not included here.
                self._last_cam_info = {
                    "frame_id": str(msg.header.frame_id),
                    "width": int(msg.width),
                    "height": int(msg.height),
                    "k": list(msg.k),
                }
                self._emit_state({"camera_info": self._last_cam_info})

            def _on_odom(self, msg: Odometry) -> None:
                try:
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
                    self._emit_state({"base": base})
                except Exception:
                    return

            def _on_imu(self, msg: Imu) -> None:
                try:
                    q = msg.orientation
                    yaw = self._yaw_from_quat(float(q.x), float(q.y), float(q.z), float(q.w))
                    self._last_imu = {
                        "yaw": float(yaw),
                        "wx": float(msg.angular_velocity.x),
                        "wy": float(msg.angular_velocity.y),
                        "wz": float(msg.angular_velocity.z),
                        "ax": float(msg.linear_acceleration.x),
                        "ay": float(msg.linear_acceleration.y),
                        "az": float(msg.linear_acceleration.z),
                    }
                    self._emit_state({"imu": self._last_imu})
                except Exception:
                    return

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

    def send_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous command publish. Use from asyncio via run_in_executor if needed."""
        if self._node is None:
            return {"success": False, "error": "ROS2 node not running"}

        from geometry_msgs.msg import PoseStamped, Twist
        from std_msgs.msg import Bool, Int32, String
        from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

        t = str(cmd.get("type") or "")
        if t == "move_robot":
            msg = PoseStamped()
            msg.header.frame_id = self.cfg.world_frame
            msg.header.stamp = self._node.get_clock().now().to_msg()
            msg.pose.position.x = float(cmd.get("x", 0.0))
            msg.pose.position.y = float(cmd.get("y", 0.0))
            msg.pose.position.z = float(cmd.get("z", 0.0))
            msg.pose.orientation.w = 1.0
            self._pub_ee.publish(msg)
            return {"success": True}
        if t == "gripper":
            msg = Bool()
            msg.data = bool(cmd.get("open", True))
            self._pub_gripper.publish(msg)
            return {"success": True}
        if t == "cmd_vel":
            msg = Twist()
            msg.linear.x = float(cmd.get("vx", 0.0))
            msg.linear.y = float(cmd.get("vy", 0.0))
            msg.angular.z = float(cmd.get("wz", 0.0))
            self._pub_cmd_vel.publish(msg)
            return {"success": True}
        if t == "walker_mode":
            if getattr(self, "_pub_walker_mode", None) is None:
                return {"success": False, "error": "walker_mode_topic not configured for ROS2 bridge"}
            mode = str(cmd.get("mode") or "").strip().lower()
            if mode not in ("external", "goal"):
                return {"success": False, "error": "walker_mode must be 'external' or 'goal'"}
            msg = String()
            msg.data = mode
            self._pub_walker_mode.publish(msg)
            return {"success": True, "mode": mode}
        if t in ("set_joint_targets", "joint_targets", "set_angles"):
            if getattr(self, "_pub_joint_traj", None) is None:
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
            return {"success": True}
        if t == "reset_scene" and self._pub_reset is not None:
            msg = Int32()
            msg.data = int(cmd.get("seed", 0))
            self._pub_reset.publish(msg)
            return {"success": True}

        # ignore unsupported commands
        return {"success": False, "error": f"Unsupported command type: {t}"}


