import asyncio
import sys
import os
import json
import time
import websockets
import numpy as np
import traceback
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

try:
    # Optional ROS2 bridge (only used when enabled via env and rclpy is installed)
    from ros2_bridge import Ros2Bridge, Ros2BridgeConfig  # type: ignore
except Exception:
    Ros2Bridge = None  # type: ignore
    Ros2BridgeConfig = None  # type: ignore

# Load environment variables from .env file in the root or parent directory
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

# Initialize MCP
mcp = FastMCP("GIL Robot Controls")

class RobotControlServer:
    def __init__(self):
        # Track per-websocket client kind ('arm' or 'humanoid'), inferred from incoming messages
        self.connected_clients = set()
        self._client_kind = {}

        # Optional ROS2 backend (disabled by default)
        self._use_ros2 = str(os.getenv("GIL_USE_ROS2", "")).strip().lower() in ("1", "true", "yes", "on")
        self._ros2_bridge = None

        # Per-kind state snapshots (merged from incoming messages)
        self.state_by_kind = {
            "arm": {
                "robot_kind": "arm",
                "joints": {},
                "end_effector": {},
                "gripper_open": True,
                "base": {},
                "sensors": {},
                "last_image": None,  # data:image/*;base64,...
                "last_image_left": None,
                "last_image_right": None,
                "last_image_wide": None,
                "camera_info": None,
                "camera_info_wide": None,
                "images": {},
            },
            "humanoid": {
                "robot_kind": "humanoid",
                "joints": {},
                "end_effector": {},
                "gripper_open": True,
                "base": {},
                "sensors": {},
                "last_image": None,
                "last_image_left": None,
                "last_image_right": None,
                "last_image_wide": None,
                "camera_info": None,
                "camera_info_wide": None,
                "images": {},
            },
        }

        # Optional mode tracking (best-effort; authoritative state comes from Isaac extension)
        self.humanoid_mode = "external"

        if self._use_ros2:
            self._init_ros2_bridge()

    def _init_ros2_bridge(self) -> None:
        if Ros2Bridge is None or Ros2BridgeConfig is None:
            raise RuntimeError(
                "GIL_USE_ROS2 is enabled but ROS2 bridge is unavailable. "
                "Install ROS2 Python deps (rclpy, messages) and ensure they are importable."
            )

        def _topic(name: str, default: str) -> str:
            v = os.getenv(name)
            return str(v).strip() if v is not None and str(v).strip() else default

        # NOTE: We treat the ROS2 bridge as authoritative for humanoid state/commands when enabled.
        # Topic names can be overridden via env to match your Isaac Sim ROS2 Bridge config.
        cfg = Ros2BridgeConfig(
            joint_states_topic=_topic("GIL_ROS2_JOINT_STATES_TOPIC", "/joint_states"),
            ee_pose_topic=_topic("GIL_ROS2_EE_POSE_TOPIC", "/ee_pose"),
            odom_topic=_topic("GIL_ROS2_ODOM_TOPIC", "/odom"),
            imu_topic=_topic("GIL_ROS2_IMU_TOPIC", ""),
            image_left_topic=_topic("GIL_ROS2_IMAGE_LEFT_TOPIC", "/camera/left/image_raw"),
            image_right_topic=_topic("GIL_ROS2_IMAGE_RIGHT_TOPIC", "/camera/right/image_raw"),
            image_wide_topic=_topic("GIL_ROS2_IMAGE_WIDE_TOPIC", ""),
            camera_info_left_topic=_topic("GIL_ROS2_CAMERA_INFO_LEFT_TOPIC", "/camera/left/camera_info"),
            world_frame=_topic("GIL_ROS2_WORLD_FRAME", "world"),
            bin_frame=_topic("GIL_ROS2_BIN_FRAME", "bin"),
            cube_red_frame=_topic("GIL_ROS2_CUBE_RED_FRAME", "cube_red"),
            cube_green_frame=_topic("GIL_ROS2_CUBE_GREEN_FRAME", "cube_green"),
            cube_blue_frame=_topic("GIL_ROS2_CUBE_BLUE_FRAME", "cube_blue"),
            ee_target_topic=_topic("GIL_ROS2_EE_TARGET_TOPIC", "/ee_target"),
            gripper_topic=_topic("GIL_ROS2_GRIPPER_TOPIC", "/gripper_open"),
            cmd_vel_topic=_topic("GIL_ROS2_CMD_VEL_TOPIC", "/cmd_vel"),
            walker_mode_topic=_topic("GIL_ROS2_WALKER_MODE_TOPIC", "/humanoid/mode"),
            joint_traj_topic=_topic("GIL_ROS2_JOINT_TRAJ_TOPIC", ""),
            joint_traj_duration_s=float(os.getenv("GIL_ROS2_JOINT_TRAJ_DURATION_S", "0.25") or 0.25),
            reset_seed_topic=_topic("GIL_ROS2_RESET_SEED_TOPIC", ""),
            jpeg_quality=int(os.getenv("GIL_ROS2_JPEG_QUALITY", "80") or 80),
        )

        def on_state_update(partial: dict) -> None:
            # Merge partial state into humanoid snapshot (ROS2 is our source of truth here).
            try:
                st = self.state_by_kind.setdefault("humanoid", {"robot_kind": "humanoid"})
                st["robot_kind"] = "humanoid"
                for k, v in (partial or {}).items():
                    st[k] = v
            except Exception:
                return

        self._ros2_bridge = Ros2Bridge(cfg, on_state_update)
        self._ros2_bridge.start()
        
    async def start_server(self):
        print("[CONTROLS] Starting WebSocket Server on ws://127.0.0.1:8766")
        # Use 127.0.0.1 to avoid IPv4/IPv6 ambiguity with 'localhost'
        async with websockets.serve(self.handle_client, "127.0.0.1", 8766):
            await asyncio.Future()  # Run forever

    async def handle_client(self, websocket):
        print(f"[CONTROLS] Client connected: {websocket.remote_address}")
        self.connected_clients.add(websocket)
        self._client_kind[websocket] = None
        
        # Restore state to frontend if available
        # Prefer restoring the arm joint state (legacy frontend behavior)
        if self.state_by_kind["arm"].get("joints"):
            print("[CONTROLS] Restoring robot state to reconnected client")
            await websocket.send(json.dumps({
                "type": "set_angles",
                "angles": self.state_by_kind["arm"]["joints"]
            }))
            
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    # Infer and remember client kind for routing (arm vs humanoid)
                    kind = (data.get("robot_kind") or data.get("kind") or "").lower().strip()
                    if kind in ("arm", "humanoid"):
                        self._client_kind[websocket] = kind
                    await self.handle_message(data)
                except Exception as e:
                    print(f"[ERROR] Failed to process message: {e}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connected_clients.remove(websocket)
            self._client_kind.pop(websocket, None)
            print("[CONTROLS] Client disconnected")

    async def handle_message(self, data):
        msg_type = data.get('type')

        # Infer client kind (arm vs humanoid) if provided
        kind = (data.get("robot_kind") or data.get("kind") or "").lower().strip() or None
        
        if msg_type == 'scene_image':
            # Legacy image+joint bundle (typically 'arm' or generic client)
            target_kind = kind or "arm"
            state = self.state_by_kind.setdefault(target_kind, {"robot_kind": target_kind})
            state["robot_kind"] = target_kind
            state["last_image"] = data.get('image')
            state["last_image_left"] = data.get('image_left')
            state["last_image_right"] = data.get('image_right')
            state["camera_info"] = data.get('camera_info')
            if data.get("joint_positions") is not None:
                state["joints"] = data.get("joint_positions") or {}
            # Also store any extra images map if provided
            if data.get("images") is not None:
                state["images"] = data.get("images") or {}
            
            # Log occasionally
            if np.random.rand() < 0.05:
                print(f"[CONTROLS] State updated from Frontend")
            return

        if msg_type == "scene_state":
            # Isaac extension publishes this schema: {type:'scene_state', robot_kind, base, joints, sensors, images, ...}
            target_kind = kind or "humanoid"
            state = self.state_by_kind.setdefault(target_kind, {"robot_kind": target_kind})
            state["robot_kind"] = target_kind
            for k in ("joints", "end_effector", "gripper_open", "base", "sensors"):
                if k in data:
                    state[k] = data.get(k)
            # Images
            if "last_image" in data:
                state["last_image"] = data.get("last_image")
            if "last_image_left" in data:
                state["last_image_left"] = data.get("last_image_left")
            if "last_image_right" in data:
                state["last_image_right"] = data.get("last_image_right")
            if "last_image_wide" in data:
                state["last_image_wide"] = data.get("last_image_wide")
            if "camera_info" in data:
                state["camera_info"] = data.get("camera_info")
            if "camera_info_wide" in data:
                state["camera_info_wide"] = data.get("camera_info_wide")
            if "images" in data:
                state["images"] = data.get("images") or {}
            # Store websocket kind if we can
            # (We don't have the websocket here; best-effort mapping is handled in handle_client loop below)
            if np.random.rand() < 0.05:
                print(f"[CONTROLS] State updated from {target_kind}")
            return

    def _choose_default_kind(self) -> str:
        # Prefer humanoid if we have recent data; otherwise fall back to arm
        if self.state_by_kind.get("humanoid", {}).get("last_image") or self.state_by_kind.get("humanoid", {}).get("base"):
            return "humanoid"
        return "arm"

    def _client_kinds_present(self) -> set:
        kinds = set()
        for k in self._client_kind.values():
            if k:
                kinds.add(k)
        return kinds

    async def send_command(self, command_data, robot_kind: str | None = None):
        # When ROS2 backend is enabled, publish synchronously to ROS2 topics.
        if self._use_ros2 and self._ros2_bridge is not None:
            try:
                return self._ros2_bridge.send_command(command_data)
            except Exception as e:
                return {"success": False, "error": f"ROS2 send_command failed: {e}"}

        if not self.connected_clients:
            return {"success": False, "error": "No robot connected"}

        target_kind = (robot_kind or "").lower().strip() or None
        message = json.dumps(command_data)

        # If no kind requested, broadcast (legacy behavior)
        if not target_kind:
            for ws in list(self.connected_clients):
                await ws.send(message)
            return {"success": True}

        sent = 0
        for ws in list(self.connected_clients):
            if self._client_kind.get(ws) == target_kind:
                await ws.send(message)
                sent += 1

        # If we haven't inferred any kinds yet, fall back to broadcast so we don't deadlock controls
        if sent == 0:
            for ws in list(self.connected_clients):
                await ws.send(message)
            return {"success": True, "warning": f"No client tagged as '{target_kind}'. Broadcasted command instead."}

        return {"success": True, "sent": sent}

# Global instance
robot_server = RobotControlServer()

@mcp.tool()
async def move_arm(x: float, y: float, z: float, reason: str = "") -> str:
    """Move the robot arm to coordinates.
    Args:
        x: X position (meters)
        y: Y position (meters)
        z: Z position (meters)
        reason: Explanation for the movement
    """
    print(f"[TOOL] Move Arm: {x}, {y}, {z} ({reason})")
    print(f"[DEBUG] Connected clients: {len(robot_server.connected_clients)}")
    
    # SAFETY CONSTRAINT: Ground Collision Prevention
    SAFE_HEIGHT = 0.05
    clamped = False
    if y < SAFE_HEIGHT:
        print(f"[SAFETY] Clamping Y from {y} to {SAFE_HEIGHT} to prevent ground collision")
        y = SAFE_HEIGHT
        clamped = True
    
    # Check connection status
    if (not robot_server.connected_clients) and (not getattr(robot_server, "_use_ros2", False)):
        return json.dumps({
            "status": "error", 
            "error": "No robot connected. Please ensure the Frontend is running and connected to WebSocket port 8766, "
                     "or enable ROS2 backend with GIL_USE_ROS2=1."
        })
        
    result = await robot_server.send_command({
        "type": "move_robot",
        "x": x, "y": y, "z": z
    })
    
    if not result.get("success"):
        return json.dumps({"status": "error", "error": result.get("error")})
        
    # Wait a bit for movement (simulation is fast but let's be safe)
    await asyncio.sleep(1.0)
    
    response = {"status": "success", "target": {"x": x, "y": y, "z": z}}
    if clamped:
        response["warning"] = f"Target Y was clamped to safe height {SAFE_HEIGHT}m."
        
    return json.dumps(response)

@mcp.tool()
async def control_gripper(action: str) -> str:
    """Control the gripper.
    Args:
        action: 'open' or 'close'
    """
    print(f"[TOOL] Gripper: {action}")
    is_open = (action.lower() == "open")
    await robot_server.send_command({
        "type": "gripper",
        "open": is_open
    })
    await asyncio.sleep(0.5)
    return json.dumps({"status": "success", "action": action})

@mcp.tool()
async def get_robot_state() -> str:
    """Get full robot state including joint angles and end effector position."""
    kind = robot_server._choose_default_kind()
    return json.dumps(robot_server.state_by_kind.get(kind, {}))

@mcp.tool()
async def get_robot_state_for(robot_kind: str) -> str:
    """Get robot state for a specific robot kind ('arm' or 'humanoid')."""
    k = (robot_kind or "").lower().strip()
    return json.dumps(robot_server.state_by_kind.get(k, {"error": f"Unknown robot_kind '{robot_kind}'"}))

@mcp.tool()
async def get_latest_image() -> str:
    """Get the latest camera image from the robot.
    Returns a dict with 'image' (base64), 'image_left', 'image_right', 'camera_info'.
    """
    kind = robot_server._choose_default_kind()
    state = robot_server.state_by_kind.get(kind, {})
    if not state.get('last_image'):
        return json.dumps({"error": "No image available"})
    return json.dumps({
        "image": state.get('last_image'),
        "image_left": state.get('last_image_left'),
        "image_right": state.get('last_image_right'),
        "image_wide": state.get('last_image_wide'),
        "camera_info": state.get('camera_info'),
        "camera_info_wide": state.get('camera_info_wide'),
        "images": state.get("images") or {},
    })

@mcp.tool()
async def get_latest_image_for(robot_kind: str) -> str:
    """Get the latest camera image for a specific robot kind ('arm' or 'humanoid')."""
    k = (robot_kind or "").lower().strip()
    state = robot_server.state_by_kind.get(k, {})
    if not state.get('last_image'):
        return json.dumps({"error": "No image available"})
    return json.dumps({
        "image": state.get('last_image'),
        "image_left": state.get('last_image_left'),
        "image_right": state.get('last_image_right'),
        "image_wide": state.get('last_image_wide'),
        "camera_info": state.get('camera_info'),
        "camera_info_wide": state.get('camera_info_wide'),
        "images": state.get("images") or {},
    })

@mcp.tool()
async def set_humanoid_mode(mode: str) -> str:
    """Set humanoid control mode.
    mode:
      - 'goal': Isaac extension steers toward a goal marker
      - 'external': Isaac extension follows latest cmd_vel commands
    """
    m = (mode or "").lower().strip()
    if m not in ("goal", "external"):
        return json.dumps({"status": "error", "error": "mode must be 'goal' or 'external'"})
    robot_server.humanoid_mode = m
    result = await robot_server.send_command({"type": "walker_mode", "mode": m}, robot_kind="humanoid")
    return json.dumps({"status": "success", "mode": m, "result": result})

@mcp.tool()
async def drive_humanoid(vx: float, vy: float, wz: float, duration_s: float = 0.25, reason: str = "") -> str:
    """Drive the humanoid base using cmd_vel-style commands."""
    print(f"[TOOL] drive_humanoid vx={vx} vy={vy} wz={wz} dur={duration_s} ({reason})")
    # Isaac-side walker uses a cmd timeout (default ~0.6s). To keep motion continuous for duration_s,
    # we re-send cmd_vel at a small rate during the requested duration.
    try:
        d = float(duration_s)
    except Exception:
        d = 0.25
    d = max(0.0, min(30.0, d))
    period_s = 0.1  # 10 Hz keepalive

    cmd_msg = {"type": "cmd_vel", "vx": float(vx), "vy": float(vy), "wz": float(wz)}
    stop_msg = {"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": 0.0}

    async def _drive_for(duration: float):
        # Always send at least once immediately
        try:
            await robot_server.send_command(cmd_msg, robot_kind="humanoid")
        except Exception:
            return
        if duration <= 0:
            return
        t_end = time.time() + duration
        while time.time() < t_end:
            await asyncio.sleep(period_s)
            try:
                await robot_server.send_command(cmd_msg, robot_kind="humanoid")
            except Exception:
                break
        try:
            await robot_server.send_command(stop_msg, robot_kind="humanoid")
        except Exception:
            pass

    # IMPORTANT: block until the drive finishes so callers can reliably query state "after" motion.
    await _drive_for(d)
    return json.dumps({"status": "success", "duration_s": d})

@mcp.tool()
async def set_humanoid_goal(x: float, y: float) -> str:
    """Set the humanoid goal position (used in 'goal' mode)."""
    result = await robot_server.send_command({"type": "set_goal", "x": float(x), "y": float(y)}, robot_kind="humanoid")
    return json.dumps({"status": "success", "goal": {"x": x, "y": y}, "result": result})

@mcp.tool()
async def reset_humanoid_episode() -> str:
    """Reset humanoid episode (maze task)."""
    result = await robot_server.send_command({"type": "reset_episode"}, robot_kind="humanoid")
    return json.dumps({"status": "success", "result": result})

@mcp.tool()
async def get_robot_config() -> str:
    """Get robot physical configuration (link lengths and limits).
    Useful for calculating reach and avoiding collisions.
    """
    return json.dumps({
        "dimensions": {
            "L1_base_height": 0.5,
            "L2_upper_arm": 0.8,
            "L3_forearm": 0.8,
            "L4_wrist_gripper": 0.2
        },
        "limits": {
            "reach_max": 1.6, # L2+L3
            "reach_min": 0.3,
            "height_max": 2.1,
            "height_min": 0.05
        },
        "workspace": "Donut shape 0.3m to 1.6m radius. Table at Y=0. The robot arm is WHITE.",
        "description": "A white robotic arm with a gripper, mounted on a base at (0,0,0)."
    })

async def main():
    import uvicorn
    
    # Start WebSocket server in background
    server_task = asyncio.create_task(robot_server.start_server())
    
    # Start MCP server (Streamable HTTP) using explicit uvicorn run to control port.
    # Cursor/MCP clients in this repo expect http://127.0.0.1:6769/mcp/
    print("[CONTROLS] Starting MCP Server (Streamable HTTP) on Port 6769...")
    # FastMCP exposes streamable_http_app as an ASGI app factory in some versions; tell uvicorn to treat it as such.
    config = uvicorn.Config(mcp.streamable_http_app, host="0.0.0.0", port=6769, factory=True)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
