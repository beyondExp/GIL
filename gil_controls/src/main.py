import asyncio
import base64
import json
import os
import time
import math
import re
from typing import Any

import websockets
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from control_backends import Ros2ControlBackend, WebSocketSimBackend
from humanoid_contract import command_envelope, default_robot_state, merge_state_update, recompute_health, sanitized_state
from safety_supervisor import SafetyConfig, SafetySupervisor
from image_gate import image_gate
from episode_recorder import EpisodeRecorder

try:
    from ros2_bridge import Ros2Bridge, Ros2BridgeConfig  # type: ignore
except Exception:
    Ros2Bridge = None  # type: ignore
    Ros2BridgeConfig = None  # type: ignore

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

# Use stateless StreamableHTTP to make long-running tool sessions more robust
# against transient connection drops (each request uses a fresh transport).
mcp = FastMCP("GIL Robot Controls", stateless_http=True)

_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")


def _normalize_image_data_url(value: Any) -> str:
    """Normalize a camera payload into a data URL if possible."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        b64 = base64.b64encode(bytes(value)).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    if not isinstance(value, str):
        return ""
    s = value.strip()
    if not s:
        return ""
    if s.startswith("data:image"):
        return s
    if s.startswith("/9j/"):
        return f"data:image/jpeg;base64,{s}"
    if s.startswith("iVBOR"):
        return f"data:image/png;base64,{s}"
    if len(s) >= 256 and _BASE64_RE.match(s):
        compact = s.replace("\r", "").replace("\n", "")
        return f"data:image/jpeg;base64,{compact}"
    return ""


def _normalize_images_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in value.items():
        norm = _normalize_image_data_url(v)
        out[str(k)] = norm if norm else v
    return out


class RobotControlServer:
    def __init__(self):
        self.connected_clients = set()
        self._client_kind: dict[Any, str | None] = {}
        # Enforce one active websocket per robot kind to avoid racing state updates
        # when a simulator reconnects (or double-connects) during restarts.
        self._active_client_by_kind: dict[str, Any] = {}
        self._use_ros2 = str(os.getenv("GIL_USE_ROS2", "")).strip().lower() in ("1", "true", "yes", "on")
        self._backend_mode = (os.getenv("GIL_HUMANOID_BACKEND", "").strip().lower() or ("sim_ros2" if self._use_ros2 else "sim_ws"))
        self._ros2_bridge = None
        self._event_log_path = os.getenv(
            "GIL_EVENT_LOG_PATH",
            os.path.join(os.path.dirname(__file__), "..", "logs", "humanoid_events.jsonl"),
        )
        os.makedirs(os.path.dirname(os.path.abspath(self._event_log_path)), exist_ok=True)
        self.state_by_kind = {
            "arm": default_robot_state("arm"),
            "humanoid": default_robot_state("humanoid"),
        }
        rec_root = os.getenv(
            "GIL_RECORDINGS_DIR",
            os.path.join(os.path.dirname(__file__), "..", "..", "assets", "recordings"),
        )
        rec_period_ms = int(os.getenv("GIL_RECORD_SAMPLE_PERIOD_MS", "250") or 250)
        self.recorder = EpisodeRecorder(root_dir=str(rec_root), sample_period_ms=rec_period_ms)
        self.humanoid_mode = "external"
        self.safety = SafetySupervisor(
            SafetyConfig(
                stale_state_after_s=float(os.getenv("GIL_STATE_STALE_AFTER_S", "1.5") or 1.5),
                heartbeat_timeout_s=float(os.getenv("GIL_HEARTBEAT_TIMEOUT_S", "2.0") or 2.0),
                max_linear_velocity_mps=float(os.getenv("GIL_MAX_VX", "0.6") or 0.6),
                max_lateral_velocity_mps=float(os.getenv("GIL_MAX_VY", "0.4") or 0.4),
                max_angular_velocity_rps=float(os.getenv("GIL_MAX_WZ", "1.0") or 1.0),
                max_drive_duration_s=float(os.getenv("GIL_MAX_DRIVE_DURATION_S", "10.0") or 10.0),
                min_upright_base_z_m=float(os.getenv("GIL_MIN_UPRIGHT_BASE_Z", "0.55") or 0.55),
                disable_motion_on_fall=str(os.getenv("GIL_DISABLE_MOTION_ON_FALL", "1") or "1").strip().lower()
                in ("1", "true", "yes", "on"),
                require_motion_enable=str(os.getenv("GIL_REQUIRE_MOTION_ENABLE", "1")).strip().lower() not in ("0", "false", "no"),
                allow_sim_preview=self._backend_mode != "h1_hardware",
            )
        )

        if self._use_ros2:
            self._init_ros2_bridge()

        if self._backend_mode == "sim_ws":
            self._backend = WebSocketSimBackend(
                connected_clients=lambda: (list(self._active_client_by_kind.values()) or list(self.connected_clients)),
                client_kind=lambda ws: self._client_kind.get(ws),
            )
        elif self._ros2_bridge is not None:
            self._backend = Ros2ControlBackend(self._ros2_bridge, hardware_mode=self._backend_mode == "h1_hardware")
        else:
            raise RuntimeError(f"Unsupported backend mode '{self._backend_mode}'")

    def _record_event(self, event_type: str, **payload: Any) -> None:
        entry = {
            "event_type": event_type,
            "recorded_at_ms": int(time.time() * 1000),
            "backend": self._backend.backend_name,
            **payload,
        }
        try:
            with open(self._event_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _topic(self, name: str, default: str) -> str:
        value = os.getenv(name)
        return str(value).strip() if value is not None and str(value).strip() else default

    def _init_ros2_bridge(self) -> None:
        if Ros2Bridge is None or Ros2BridgeConfig is None:
            raise RuntimeError(
                "GIL_USE_ROS2 is enabled but ROS2 bridge is unavailable. "
                "Install ROS2 Python deps (rclpy, messages) and ensure they are importable."
            )

        cfg = Ros2BridgeConfig(
            joint_states_topic=self._topic("GIL_ROS2_JOINT_STATES_TOPIC", "/joint_states"),
            ee_pose_topic=self._topic("GIL_ROS2_EE_POSE_TOPIC", "/ee_pose"),
            odom_topic=self._topic("GIL_ROS2_ODOM_TOPIC", "/odom"),
            imu_topic=self._topic("GIL_ROS2_IMU_TOPIC", ""),
            battery_state_topic=self._topic("GIL_ROS2_BATTERY_TOPIC", ""),
            fault_state_topic=self._topic("GIL_ROS2_FAULT_TOPIC", ""),
            estop_state_topic=self._topic("GIL_ROS2_ESTOP_TOPIC", ""),
            robot_mode_state_topic=self._topic("GIL_ROS2_MODE_STATE_TOPIC", ""),
            image_left_topic=self._topic("GIL_ROS2_IMAGE_LEFT_TOPIC", "/camera/left/image_raw"),
            image_right_topic=self._topic("GIL_ROS2_IMAGE_RIGHT_TOPIC", "/camera/right/image_raw"),
            image_wide_topic=self._topic("GIL_ROS2_IMAGE_WIDE_TOPIC", ""),
            camera_info_left_topic=self._topic("GIL_ROS2_CAMERA_INFO_LEFT_TOPIC", "/camera/left/camera_info"),
            world_frame=self._topic("GIL_ROS2_WORLD_FRAME", "world"),
            bin_frame=self._topic("GIL_ROS2_BIN_FRAME", "bin"),
            cube_red_frame=self._topic("GIL_ROS2_CUBE_RED_FRAME", "cube_red"),
            cube_green_frame=self._topic("GIL_ROS2_CUBE_GREEN_FRAME", "cube_green"),
            cube_blue_frame=self._topic("GIL_ROS2_CUBE_BLUE_FRAME", "cube_blue"),
            ee_target_topic=self._topic("GIL_ROS2_EE_TARGET_TOPIC", "/ee_target"),
            gripper_topic=self._topic("GIL_ROS2_GRIPPER_TOPIC", "/gripper_open"),
            cmd_vel_topic=self._topic("GIL_ROS2_CMD_VEL_TOPIC", "/cmd_vel"),
            walker_mode_topic=self._topic("GIL_ROS2_WALKER_MODE_TOPIC", "/humanoid/mode"),
            joint_traj_topic=self._topic("GIL_ROS2_JOINT_TRAJ_TOPIC", ""),
            joint_traj_duration_s=float(os.getenv("GIL_ROS2_JOINT_TRAJ_DURATION_S", "0.25") or 0.25),
            reset_seed_topic=self._topic("GIL_ROS2_RESET_SEED_TOPIC", ""),
            jpeg_quality=int(os.getenv("GIL_ROS2_JPEG_QUALITY", "80") or 80),
            stale_after_s=self.safety.cfg.stale_state_after_s,
        )
        self._ros2_bridge = Ros2Bridge(cfg, self._on_ros2_state_update)
        self._ros2_bridge.start()

    def _refresh_health(self, robot_kind: str) -> dict[str, Any]:
        state = self.state_by_kind.setdefault(robot_kind, default_robot_state(robot_kind))
        return recompute_health(
            state,
            stale_after_s=self.safety.cfg.stale_state_after_s,
            motion_enabled=self.safety.motion_enabled if robot_kind == "humanoid" else True,
            estop=self.safety.estop_active if robot_kind == "humanoid" else False,
            backend=self._backend.backend_name,
        )

    def _merge_update(self, robot_kind: str, partial: dict[str, Any], *, source: str) -> None:
        state = self.state_by_kind.setdefault(robot_kind, default_robot_state(robot_kind))
        merge_state_update(
            state,
            partial,
            source=source,
            backend=self._backend.backend_name,
            stale_after_s=self.safety.cfg.stale_state_after_s,
            motion_enabled=self.safety.motion_enabled if robot_kind == "humanoid" else True,
            estop=self.safety.estop_active if robot_kind == "humanoid" else False,
        )
        try:
            self.recorder.maybe_record(robot_kind=robot_kind, state=state)
        except Exception:
            pass

    def _on_ros2_state_update(self, partial: dict[str, Any]) -> None:
        try:
            source = str(partial.get("_source") or "ros2")
            self._merge_update("humanoid", partial, source=source)
            safety = self.state_by_kind["humanoid"].get("safety") or {}
            if safety.get("estop") and not self.safety.estop_active:
                self.safety.set_estop(True, "ros2_estop_topic")
        except Exception:
            return

    async def start_server(self):
        bind_host = (os.getenv("GIL_WS_BIND") or "127.0.0.1").strip() or "127.0.0.1"
        bind_port = int(os.getenv("GIL_WS_PORT") or "8766")
        print(f"[CONTROLS] Starting WebSocket Server on ws://{bind_host}:{bind_port}")
        async with websockets.serve(self.handle_client, bind_host, bind_port):
            await asyncio.Future()

    async def watchdog_loop(self):
        auto_heartbeat = str(os.getenv("GIL_AUTO_HEARTBEAT", "0")).strip().lower() in ("1", "true", "yes", "on")
        while True:
            await asyncio.sleep(0.2)
            if auto_heartbeat:
                self.safety.heartbeat(source="auto")
            should_stop, reason = self.safety.should_force_stop()
            if should_stop:
                await self._send_stop_now(reason=reason)

    async def handle_client(self, websocket):
        print(f"[CONTROLS] Client connected: {websocket.remote_address}")
        # Require a hello handshake quickly; this prevents random clients (e.g. browsers) from
        # being counted as "simulation connected" without ever streaming state.
        self._client_kind[websocket] = None
        try:
            hs = float(os.getenv("GIL_WS_HANDSHAKE_TIMEOUT_S", "15.0") or 15.0)
            first = await asyncio.wait_for(websocket.recv(), timeout=hs)
        except Exception:
            try:
                await websocket.close()
            except Exception:
                pass
            self._client_kind.pop(websocket, None)
            print("[CONTROLS] Client rejected (no handshake)")
            return

        try:
            first_data = json.loads(first)
        except Exception:
            try:
                await websocket.close()
            except Exception:
                pass
            self._client_kind.pop(websocket, None)
            print("[CONTROLS] Client rejected (bad handshake)")
            return

        msg_type = str(first_data.get("type") or "")
        kind = (first_data.get("robot_kind") or first_data.get("kind") or "").lower().strip()
        # Backwards-compatible handshake:
        # - preferred: explicit {"type":"hello","robot_kind":"humanoid"|"arm"}
        # - legacy: first message is already a scene_state/scene_image
        inferred_kind: str | None = None
        is_legacy = False
        if msg_type == "hello" and kind in ("arm", "humanoid"):
            inferred_kind = kind
        elif msg_type in ("scene_state", "scene_image"):
            is_legacy = True
            if kind in ("arm", "humanoid"):
                inferred_kind = kind
            else:
                inferred_kind = "humanoid" if msg_type == "scene_state" else "arm"
        else:
            inferred_kind = None

        if inferred_kind not in ("arm", "humanoid"):
            try:
                await websocket.close()
            except Exception:
                pass
            self._client_kind.pop(websocket, None)
            print(f"[CONTROLS] Client rejected (unexpected hello): type={msg_type!r} kind={kind!r}")
            return

        # Handshake accepted (or legacy accepted).
        self.connected_clients.add(websocket)
        self._client_kind[websocket] = inferred_kind
        if is_legacy:
            print(f"[CONTROLS] Client accepted (legacy, no hello): type={msg_type!r} kind={inferred_kind!r}")

        # Ensure it is the single active client for that kind.
        prev = self._active_client_by_kind.get(inferred_kind)
        if prev is not None and prev is not websocket:
            try:
                await prev.close()
            except Exception:
                pass
            self.connected_clients.discard(prev)
            self._client_kind.pop(prev, None)
        self._active_client_by_kind[inferred_kind] = websocket

        # Reply to arm with current joints on connect (after handshake).
        if inferred_kind == "arm" and self.state_by_kind["arm"].get("joints"):
            await websocket.send(json.dumps({"type": "set_angles", "angles": self.state_by_kind["arm"]["joints"]}))

        try:
            # Process the hello message (best-effort; it is ok if handler ignores it).
            try:
                await self.handle_message(first_data)
            except Exception:
                pass

            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_message(data)
                except Exception as exc:
                    print(f"[ERROR] Failed to process message: {exc}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connected_clients.discard(websocket)
            kind = self._client_kind.pop(websocket, None)
            if kind and self._active_client_by_kind.get(kind) is websocket:
                self._active_client_by_kind.pop(kind, None)
            print("[CONTROLS] Client disconnected")

    async def handle_message(self, data):
        msg_type = str(data.get("type") or "")
        kind = (data.get("robot_kind") or data.get("kind") or "").lower().strip() or None

        if msg_type == "scene_image":
            target_kind = kind or "arm"
            partial = {
                "last_image": _normalize_image_data_url(data.get("image")),
                "last_image_left": _normalize_image_data_url(data.get("image_left")),
                "last_image_right": _normalize_image_data_url(data.get("image_right")),
                "last_image_wide": _normalize_image_data_url(data.get("image_wide")),
                "camera_info": data.get("camera_info"),
                "camera_info_wide": data.get("camera_info_wide"),
                "images": _normalize_images_dict(data.get("images") or {}),
                "status": "streaming",
            }
            if data.get("joint_positions") is not None:
                partial["joints"] = data.get("joint_positions") or {}
            self._merge_update(target_kind, partial, source="websocket:scene_image")
            return

        if msg_type == "scene_state":
            target_kind = kind or "humanoid"
            partial = {
                "status": "streaming",
            }
            for key in (
                "joints",
                "joint_positions",
                "end_effector",
                "gripper_open",
                "base",
                "sensors",
                "last_image",
                "last_image_left",
                "last_image_right",
                "last_image_wide",
                "camera_info",
                "camera_info_wide",
                "images",
                "imu",
                "battery",
                "faults",
                "mode",
            ):
                if key in data:
                    if key == "joint_positions":
                        partial["joints"] = data.get("joint_positions") or {}
                    else:
                        partial[key] = data.get(key)
            # Normalize camera payloads if present.
            if "last_image" in partial:
                partial["last_image"] = _normalize_image_data_url(partial.get("last_image"))
            if "last_image_left" in partial:
                partial["last_image_left"] = _normalize_image_data_url(partial.get("last_image_left"))
            if "last_image_right" in partial:
                partial["last_image_right"] = _normalize_image_data_url(partial.get("last_image_right"))
            if "last_image_wide" in partial:
                partial["last_image_wide"] = _normalize_image_data_url(partial.get("last_image_wide"))
            if "images" in partial:
                partial["images"] = _normalize_images_dict(partial.get("images"))
            self._merge_update(target_kind, partial, source="websocket:scene_state")
            return

    def _choose_default_kind(self) -> str:
        # Prefer whichever kind is currently healthy/streaming.
        # This avoids sticky defaults when an old humanoid state exists but the active client is an arm (or vice-versa).
        arm = self._refresh_health("arm")
        humanoid = self._refresh_health("humanoid")
        arm_ok = bool((arm.get("health") or {}).get("ok")) if isinstance(arm, dict) else False
        hum_ok = bool((humanoid.get("health") or {}).get("ok")) if isinstance(humanoid, dict) else False
        if arm_ok and not hum_ok:
            return "arm"
        if hum_ok and not arm_ok:
            return "humanoid"
        # If both are ok (or both are not ok), prefer the one with a recent image.
        if arm_ok and hum_ok:
            if arm.get("last_image") and not humanoid.get("last_image"):
                return "arm"
            if humanoid.get("last_image") and not arm.get("last_image"):
                return "humanoid"
        # Fall back: any active sources?
        if self.state_by_kind.get("arm", {}).get("meta", {}).get("sources"):
            return "arm"
        return "humanoid"

    async def _send_backend_command(self, command_data: dict[str, Any], robot_kind: str | None = None) -> dict[str, Any]:
        result = await self._backend.send_command(command_data, robot_kind=robot_kind)
        if result.get("success"):
            target_kind = robot_kind or command_data.get("robot_kind") or "arm"
            state = self.state_by_kind.setdefault(target_kind, default_robot_state(str(target_kind)))
            state["last_command"] = {
                "type": command_data.get("type"),
                "issued_at_ms": command_data.get("issued_at_ms"),
                "command_id": command_data.get("command_id"),
                "result": result,
            }
            self._record_event("command_sent", robot_kind=target_kind, command_type=command_data.get("type"), result=result)
        else:
            self._record_event("command_failed", robot_kind=robot_kind or "unknown", command_type=command_data.get("type"), result=result)
        return result

    async def _send_stop_now(self, *, reason: str) -> dict[str, Any]:
        cmd = command_envelope(
            "cmd_vel",
            robot_kind="humanoid",
            reason=reason,
            payload={"vx": 0.0, "vy": 0.0, "wz": 0.0},
        )
        result = await self._send_backend_command(cmd, robot_kind="humanoid")
        self._record_event("safety_stop", reason=reason, result=result)
        return result

    def get_preflight(self) -> dict[str, Any]:
        if self._backend_mode == "sim_ws":
            return self._backend.preflight_check()
        return self._backend.preflight_check()

    def get_humanoid_health(self) -> dict[str, Any]:
        state = sanitized_state(self._refresh_health("humanoid"))
        return {
            "backend": self._backend.backend_name,
            "backend_role": self._backend.backend_role,
            "preflight": self.get_preflight(),
            "safety": self.safety.build_status(),
            "state_health": state.get("health") or {},
            "mode": self.humanoid_mode,
        }

# Global instance
robot_server = RobotControlServer()


def _inspector_allowed_origins() -> list[str]:
    raw = str(os.getenv("GIL_INSPECTOR_CORS_ORIGINS", "") or "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    # Safe dev defaults (gods-eye-view vite).
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]


def _inspector_allow_origin_regex() -> str | None:
    """Allow any localhost/127.0.0.1 port in dev by default.

    This supports docker port mappings (e.g. 4174 -> 4173) without forcing
    operators to keep updating a port allowlist.
    """
    raw = str(os.getenv("GIL_INSPECTOR_CORS_ORIGINS", "") or "").strip()
    if raw:
        # Explicit allowlist provided; no regex.
        return None
    return r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def _predict_path_local_from_cmd_vel(
    *,
    x: float,
    y: float,
    yaw: float,
    commands: list[dict[str, Any]],
    max_points: int = 240,
) -> list[dict[str, float]]:
    """Predict a local XY path from cmd_vel pulses (for inspector rendering)."""
    try:
        from gil.world.twin import UnicycleTwin
    except Exception:
        return []

    twin = UnicycleTwin()
    twin.pose.x = float(x)
    twin.pose.y = float(y)
    twin.pose.yaw = float(yaw)
    pts: list[dict[str, float]] = [{"x": float(twin.pose.x), "y": float(twin.pose.y), "z": 0.0}]
    # Sample at a stable cadence regardless of variable dt.
    sample_dt = 0.25
    for cmd in commands or []:
        if len(pts) >= max_points:
            break
        vx = float(cmd.get("vx", 0.0) or 0.0)
        vy = float(cmd.get("vy", 0.0) or 0.0)
        wz = float(cmd.get("wz", 0.0) or 0.0)
        dt = float(cmd.get("dt", 0.2) or 0.2)
        n = max(1, int(math.ceil(dt / sample_dt)))
        sdt = dt / n
        for _ in range(n):
            if len(pts) >= max_points:
                break
            twin.step(vx, vy, wz, sdt, occupancy=None)
            pts.append({"x": float(twin.pose.x), "y": float(twin.pose.y), "z": 0.0})
    return pts


def _build_inspector_snapshot(robot_kind: str = "") -> dict[str, Any]:
    """Single payload: observation + belief + gate + dream trace + controls health."""
    from gil.core.contracts import FrameGraph, GateSnapshot, InspectorSnapshot, ObservationEnvelope, SceneBeliefSnapshot, WorldAnchor

    kind = (robot_kind or "").strip().lower() or robot_server._choose_default_kind()
    if kind not in ("arm", "humanoid"):
        kind = "humanoid"

    state = robot_server._refresh_health(kind)
    img, _img_rep = _pick_best_image(state if isinstance(state, dict) else {})
    anchor_obj = None
    try:
        wa = (state.get("world_anchor") or {}) if isinstance(state, dict) else {}
        if isinstance(wa, dict) and ("lon" in wa) and ("lat" in wa):
            anchor_obj = WorldAnchor.model_validate(wa)
    except Exception:
        anchor_obj = None

    fg_obj = None
    try:
        fg = (state.get("frame_graph") or None) if isinstance(state, dict) else None
        if isinstance(fg, dict):
            fg_obj = FrameGraph.model_validate(fg)
    except Exception:
        fg_obj = None
    obs = ObservationEnvelope(
        morphology="arm" if kind == "arm" else "humanoid",
        state=sanitized_state(state),
        image=img or None,
        images=(state.get("images") or {}) if isinstance(state, dict) else {},
        objects=(state.get("objects") or []) if isinstance(state, dict) else [],
        anchor=anchor_obj,
        frame_graph=fg_obj,
    )

    director = _agent_director()
    gate = GateSnapshot()
    plan_cmds: list[dict[str, Any]] = []
    kept = 0
    predicted: list[dict[str, float]] = []
    try:
        if director.last_gate is not None:
            gate = GateSnapshot(
                ok=bool(getattr(director.last_gate, "ok", False)),
                reason=str(getattr(director.last_gate, "reason", "") or ""),
                gate_id=str(getattr(director.last_gate, "gate_id", "") or ""),
                scores=dict(getattr(director.last_gate, "scores", {}) or {}),
            )
        kept = int(len(getattr(director, "last_kept", []) or []))
        if kept:
            plan_cmds = list((director.last_kept[0].commands or []))  # type: ignore[index]
            base = obs.base_pose()
            predicted = _predict_path_local_from_cmd_vel(x=base.x, y=base.y, yaw=base.yaw, commands=plan_cmds)
    except Exception:
        pass

    # Belief snapshot from the orchestrator's SceneMap (if available).
    belief = SceneBeliefSnapshot()
    include_occ = str(os.getenv("GIL_INSPECTOR_INCLUDE_OCCUPANCY", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        rid = getattr(director, "robot_id", "h1")
        scene = director.orch.maps.get(rid) if getattr(director, "orch", None) else None
        if scene is not None:
            belief.calibrated = bool(getattr(scene, "calibrated", False))
            belief.coverage = float(scene.summary().get("coverage", 0.0) or 0.0)
            objs = []
            for o in (getattr(scene, "objects", []) or []):
                try:
                    objs.append({"label": str(o.label), "x": float(o.x), "y": float(o.y), "z": float(getattr(o, "z", 0.0) or 0.0), "occupied": bool(getattr(o, "occupied", True))})
                except Exception:
                    continue
            belief.objects = objs  # type: ignore[assignment]
            occ = list(getattr(scene, "occupied", set()) or [])
            belief.occupied_cells = int(len(occ))
            if include_occ:
                # Bound the payload size.
                belief.occupied = [(int(a), int(b)) for (a, b) in occ[:4000]]
    except Exception:
        pass

    payload = InspectorSnapshot(
        observation=obs,
        belief=belief,
        gate=gate,
        dream={"kept": kept, "preview_commands": plan_cmds, "predicted_path_local": predicted},
        director={
            "embodiment": getattr(getattr(director, "embodiment", None), "key", None),
            "world": None if getattr(director, "world", None) is None else {"generator": director.world.generator, "live": director.world.live},
            "runtime_stage": getattr(getattr(director, "last_snapshot", None), "runtime_stage", None),
            "decision": getattr(getattr(director, "last_snapshot", None), "decision", None),
            "reason": getattr(getattr(director, "last_snapshot", None), "reason", None),
        },
        controls={
            "health": robot_server.get_humanoid_health() if kind == "humanoid" else sanitized_state(robot_server._refresh_health("arm")).get("health"),
            "preflight": robot_server.get_preflight(),
            "mode": robot_server.humanoid_mode if kind == "humanoid" else None,
            "camera": _img_rep,
        },
    ).model_dump()
    return payload

@mcp.tool()
async def move_arm(x: float, y: float, z: float, reason: str = "") -> str:
    """Move the robot arm to coordinates.
    Args:
        x: X position (meters)
        y: Y position (meters)
        z: Z position (meters)
        reason: Explanation for the movement
    """
    validation = robot_server.safety.validate_motion(
        command_type="move_robot",
        state=robot_server._refresh_health("arm"),
        payload={"x": float(x), "y": float(y), "z": float(z)},
    )
    if not validation.get("ok"):
        return json.dumps({"status": "error", "error": validation.get("error")})
    payload = validation["payload"]
    result = await robot_server._send_backend_command(
        command_envelope("move_robot", robot_kind="arm", reason=reason, payload=payload),
        robot_kind="arm",
    )
    if not result.get("success"):
        return json.dumps({"status": "error", "error": result.get("error")})
    await asyncio.sleep(1.0)
    response = {"status": "success", "target": payload, "result": result}
    if validation.get("warning"):
        response["warning"] = validation["warning"]
    return json.dumps(response)

@mcp.tool()
async def control_gripper(action: str) -> str:
    """Control the gripper.
    Args:
        action: 'open' or 'close'
    """
    is_open = (action.lower() == "open")
    result = await robot_server._send_backend_command(
        command_envelope("gripper", robot_kind="arm", payload={"open": is_open}),
        robot_kind="arm",
    )
    await asyncio.sleep(0.5)
    return json.dumps({"status": "success", "action": action, "result": result})

@mcp.tool()
async def get_robot_state() -> str:
    """Get full robot state including joint angles and end effector position."""
    kind = robot_server._choose_default_kind()
    return json.dumps(sanitized_state(robot_server._refresh_health(kind)))

@mcp.tool()
async def get_robot_state_for(robot_kind: str) -> str:
    """Get robot state for a specific robot kind ('arm' or 'humanoid')."""
    k = (robot_kind or "").lower().strip()
    if k not in robot_server.state_by_kind:
        return json.dumps({"error": f"Unknown robot_kind '{robot_kind}'"})
    return json.dumps(sanitized_state(robot_server._refresh_health(k)))

@mcp.tool()
async def get_latest_image() -> str:
    """Get the latest camera image from the robot.
    Returns a dict with 'image' (base64), 'image_left', 'image_right', 'camera_info'.
    """
    kind = robot_server._choose_default_kind()
    state = robot_server._refresh_health(kind)
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
    if k not in robot_server.state_by_kind:
        return json.dumps({"error": f"Unknown robot_kind '{robot_kind}'"})
    state = robot_server._refresh_health(k)
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
    robot_server._merge_update("humanoid", {"mode": m}, source="controls:set_humanoid_mode")
    result = await robot_server._send_backend_command(
        command_envelope("walker_mode", robot_kind="humanoid", payload={"mode": m}),
        robot_kind="humanoid",
    )
    return json.dumps({"status": "success", "mode": m, "result": result})

@mcp.tool()
async def drive_humanoid(vx: float, vy: float, wz: float, duration_s: float = 0.25, reason: str = "") -> str:
    """Drive the humanoid base using cmd_vel-style commands."""
    robot_server.safety.heartbeat(source="drive_humanoid")
    validation = robot_server.safety.validate_motion(
        command_type="cmd_vel",
        state=robot_server._refresh_health("humanoid"),
        payload={"vx": vx, "vy": vy, "wz": wz, "duration_s": duration_s},
    )
    if not validation.get("ok"):
        # Best-effort: stop immediately (prevents "crawling while fallen" if a command was previously active).
        try:
            await robot_server._send_stop_now(reason=str(validation.get("error") or "motion_rejected"))
        except Exception:
            pass
        return json.dumps({"status": "error", "error": validation.get("error"), "details": validation})
    payload = validation["payload"]
    d = float(payload.get("duration_s", 0.25))
    period_s = 0.1  # 10 Hz keepalive

    async def _drive_for(duration: float):
        try:
            await robot_server._send_backend_command(
                command_envelope(
                    "cmd_vel",
                    robot_kind="humanoid",
                    reason=reason,
                    payload={"vx": payload["vx"], "vy": payload["vy"], "wz": payload["wz"], "dt": period_s},
                ),
                robot_kind="humanoid",
            )
        except Exception:
            return
        if duration <= 0:
            return
        t_end = time.time() + duration
        while time.time() < t_end:
            await asyncio.sleep(period_s)
            try:
                robot_server.safety.heartbeat(source="drive_humanoid_keepalive")
                # If the humanoid fell during a command, latch-stop quickly.
                st = robot_server._refresh_health("humanoid")
                base = (st.get("base") or {}) if isinstance(st, dict) else {}
                z = float(base.get("z", 0.0) or 0.0)
                minz = float(getattr(robot_server.safety.cfg, "min_upright_base_z_m", 0.55))
                if 0.0 < z < minz:
                    try:
                        robot_server.safety.set_motion_enabled(False, reason="fall_detected")
                    except Exception:
                        pass
                    await robot_server._send_stop_now(reason="fall_detected")
                    break
                await robot_server._send_backend_command(
                    command_envelope(
                        "cmd_vel",
                        robot_kind="humanoid",
                        reason=reason,
                        payload={"vx": payload["vx"], "vy": payload["vy"], "wz": payload["wz"], "dt": period_s},
                    ),
                    robot_kind="humanoid",
                )
            except Exception:
                break
        await robot_server._send_stop_now(reason="drive_complete")

    await _drive_for(d)
    return json.dumps(
        {
            "status": "success",
            "duration_s": d,
            "command": {"vx": payload["vx"], "vy": payload["vy"], "wz": payload["wz"]},
            "warning": validation.get("warning"),
        }
    )

@mcp.tool()
async def set_humanoid_goal(x: float, y: float) -> str:
    """Set the humanoid goal position (used in 'goal' mode)."""
    result = await robot_server._send_backend_command(
        command_envelope("set_goal", robot_kind="humanoid", payload={"x": float(x), "y": float(y)}),
        robot_kind="humanoid",
    )
    return json.dumps({"status": "success", "goal": {"x": x, "y": y}, "result": result})

@mcp.tool()
async def reset_humanoid_episode(
    x: float | None = None, y: float | None = None, yaw: float | None = None, z: float | None = None
) -> str:
    """Reset humanoid episode (maze task)."""
    # Match `gil.maze_env` / `src/gil/world/maze3d.py` spawn.
    # If we reset to (0,0) the humanoid can start inside walls and look like it is "sliding" while fallen.
    # Face "north" down the entrance corridor to avoid wide turning arcs in the spawn cell.
    # Spawn one cell into the maze to give the humanoid clearance from boundary walls.
    # z is pelvis height (~1.05 m). Resetting to a fallen height plants the H1 in the ground.
    # For defaults: origin (-4,-4) + cell_center(1,1) with cell_size=1.4 -> (-1.9,-1.9)
    spawn = {"x": -1.9, "y": -1.9, "yaw": 1.5707963267948966, "z": 1.05}
    if x is not None:
        spawn["x"] = float(x)
    if y is not None:
        spawn["y"] = float(y)
    if yaw is not None:
        spawn["yaw"] = float(yaw)
    if z is not None:
        spawn["z"] = float(z)
    result = await robot_server._send_backend_command(
        command_envelope("reset_episode", robot_kind="humanoid", payload=spawn),
        robot_kind="humanoid",
    )
    return json.dumps({"status": "success", "spawn": spawn, "result": result})


@mcp.tool()
async def enable_humanoid_motion(reason: str = "") -> str:
    """Enable humanoid motion after preflight checks have passed."""
    preflight = robot_server.get_preflight()
    if not preflight.get("ok"):
        return json.dumps({"status": "error", "error": "Preflight failed.", "preflight": preflight})
    # Refresh heartbeat so the watchdog doesn't immediately latch-disable motion.
    robot_server.safety.heartbeat(source="enable_humanoid_motion")
    result = robot_server.safety.set_motion_enabled(True, reason=reason or "operator_enable")
    robot_server._record_event("motion_enabled", result=result)
    return json.dumps({"status": "success", "result": result, "preflight": preflight})


@mcp.tool()
async def disable_humanoid_motion(reason: str = "") -> str:
    """Disable humanoid motion and send an immediate stop."""
    result = robot_server.safety.set_motion_enabled(False, reason=reason or "operator_disable")
    stop_result = await robot_server._send_stop_now(reason=result["reason"] or "operator_disable")
    return json.dumps({"status": "success", "result": result, "stop_result": stop_result})


@mcp.tool()
async def set_humanoid_estop(active: bool, reason: str = "") -> str:
    """Toggle the humanoid emergency stop state."""
    result = robot_server.safety.set_estop(bool(active), reason=reason or "operator_estop")
    stop_result = await robot_server._send_stop_now(reason=result["reason"] or "estop") if active else None
    robot_server._record_event("estop_changed", result=result)
    return json.dumps({"status": "success", "result": result, "stop_result": stop_result})


@mcp.tool()
async def send_humanoid_heartbeat(source: str = "mcp") -> str:
    """Refresh the humanoid safety heartbeat."""
    return json.dumps({"status": "success", "result": robot_server.safety.heartbeat(source=source)})


@mcp.tool()
async def stop_humanoid_now(reason: str = "") -> str:
    """Send an immediate humanoid stop command."""
    result = await robot_server._send_stop_now(reason=reason or "manual_stop")
    return json.dumps({"status": "success" if result.get("success") else "error", "result": result})


@mcp.tool()
async def get_health() -> str:
    """Service health for orchestrators and CI. Never implies motion authority."""
    # NOTE: We may run an "arm-only" simulation (e.g. Franka Isaac Lab tasks) where humanoid
    # health is stale/disabled. Report the healthiest active kind while still including both.
    humanoid = robot_server.get_humanoid_health()
    arm_state = sanitized_state(robot_server._refresh_health("arm"))
    humanoid_state = sanitized_state(robot_server._refresh_health("humanoid"))
    kind = robot_server._choose_default_kind()

    hum_ready = bool((humanoid.get("state_health") or {}).get("ready_for_motion"))
    arm_ready = bool((arm_state.get("health") or {}).get("ready_for_motion"))
    ready = arm_ready if kind == "arm" else hum_ready
    warnings = (
        (arm_state.get("health") or {}).get("warnings") if kind == "arm" else (humanoid.get("state_health") or {}).get("warnings")
    ) or []
    payload = {
        "ok": True,
        "service": "gil_controls",
        "role": "body",
        "backend": humanoid.get("backend"),
        "robot_kind": kind,
        "ready_for_motion": ready,
        "warnings": warnings,
        "details": {
            "humanoid": humanoid,
            "arm": arm_state,
            "humanoid_state": humanoid_state,
        },
        "safe_for_motion_authority": False,
    }
    try:
        from gil.core.health import controls_health

        report = controls_health(
            backend=str(humanoid.get("backend") or robot_server._backend.backend_name),
            ready_for_motion=ready,
            warnings=list(payload["warnings"]),
        )
        dumped = report.model_dump()
        dumped["robot_kind"] = kind
        dumped["details"] = payload["details"]
        dumped["safe_for_motion_authority"] = False
        return json.dumps(dumped)
    except Exception:
        return json.dumps(payload)


@mcp.tool()
async def get_humanoid_health() -> str:
    """Get humanoid backend, safety, and preflight health."""
    return json.dumps(robot_server.get_humanoid_health())


@mcp.tool()
async def run_humanoid_preflight() -> str:
    """Run the humanoid backend preflight check."""
    return json.dumps(robot_server.get_preflight())

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


_director = None


def _agent_director():
    global _director
    if _director is None:
        from gil.orchestrator.director import AgentDirector

        _director = AgentDirector()
    return _director


def _live_facts() -> dict:
    st = robot_server.state_by_kind.get("humanoid") or {}
    safety = robot_server.safety.build_status()
    watchdog = safety.get("watchdog") or {}
    img = st.get("last_image") or ""
    return {
        "has_image": bool(img) and str(img).startswith("data:image"),
        "base": st.get("base") or {},
        "sensors": st.get("sensors") or {},
        "has_imu": bool((st.get("sensors") or {}).get("imu")),
        "has_contacts": bool((st.get("sensors") or {}).get("contacts")),
        "estop": bool(safety.get("estop_active")),
        "heartbeat_ok": not bool(watchdog.get("timed_out")),
        "connected": bool(robot_server.connected_clients) or bool(getattr(robot_server, "_active_client_by_kind", {})),
    }


def _make_live_observation_for_director() -> dict[str, Any]:
    """Merge the current live base pose + image into the director's world observation."""
    d = _agent_director()
    st = robot_server._refresh_health("humanoid")
    base = (st.get("base") or {}) if isinstance(st, dict) else {}
    img, _rep = _pick_best_image(st)
    raw = (d.world.observation if d.world else d._maze().to_observation())  # type: ignore[attr-defined]
    obs = dict(raw or {})
    b0 = dict(obs.get("base") or {})
    b0.update(
        {
            "x": float(base.get("x", b0.get("x", 0.0)) or 0.0),
            "y": float(base.get("y", b0.get("y", 0.0)) or 0.0),
            "z": float(base.get("z", b0.get("z", 0.0)) or 0.0),
            "yaw": float(base.get("yaw", b0.get("yaw", 0.0)) or 0.0),
        }
    )
    obs["base"] = b0
    if img:
        obs["image"] = img
    return obs


def _img_gate_params() -> dict[str, Any]:
    return {
        "min_std": float(os.getenv("GIL_STEER_MIN_IMAGE_STD", "6.0") or 6.0),
        "min_mean": float(os.getenv("GIL_STEER_MIN_IMAGE_MEAN", "8.0") or 8.0),
        "max_mean": float(os.getenv("GIL_STEER_MAX_IMAGE_MEAN", "247.0") or 247.0),
        "min_w": int(os.getenv("GIL_STEER_MIN_IMAGE_W", "80") or 80),
        "min_h": int(os.getenv("GIL_STEER_MIN_IMAGE_H", "45") or 45),
    }


def _pick_best_image(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Pick the best available camera stream from the state.
    We prefer any stream that passes `image_gate`. If none pass, return the primary `last_image`.
    """
    if not isinstance(state, dict):
        return "", {"ok": False, "reason": "no_state"}

    candidates: list[tuple[str, str]] = [
        ("image", str(state.get("last_image") or "")),
        ("wide", str(state.get("last_image_wide") or "")),
        ("left", str(state.get("last_image_left") or "")),
        ("right", str(state.get("last_image_right") or "")),
    ]
    # Extra named cameras (best-effort)
    imgs = state.get("images") or {}
    if isinstance(imgs, dict):
        for k, v in list(imgs.items()):
            if isinstance(v, str) and v.startswith("data:image"):
                candidates.append((f"extra:{k}", v))

    params = _img_gate_params()
    for label, img in candidates:
        rep = image_gate(img, **params)
        if rep.ok:
            d = rep.__dict__
            d["picked"] = label
            return img, d

    # None passed; return primary for debugging.
    primary = str(state.get("last_image") or "")
    rep0 = image_gate(primary, **params).__dict__
    rep0["picked"] = "image"
    rep0["candidates"] = [c[0] for c in candidates if c[1]]
    return primary, rep0


async def _closed_loop_commit(*, hz: float = 8.0, max_s: float = 180.0) -> dict[str, Any]:
    """Closed-loop execute: perceive -> choose next goal -> act, with hard image gating."""
    d = _agent_director()
    # Only implemented for humanoid maze today.
    maze = d._maze()  # type: ignore[attr-defined]
    try:
        goal = d.orch.sessions.get(d.robot_id).goal or {}
    except Exception:
        goal = {}
    goal_x = float(goal.get("x", maze.goal[0]) or maze.goal[0])
    goal_y = float(goal.get("y", maze.goal[1]) or maze.goal[1])
    goal_r = float(goal.get("radius", maze.spec.goal_radius) or maze.spec.goal_radius)

    period = 1.0 / max(1.0, float(hz))
    t0 = time.time()
    steps = 0
    last_waypoint = None

    # Pre-check image before enabling motion.
    st0 = robot_server._refresh_health("humanoid")
    img0, rep0 = _pick_best_image(st0)
    if not rep0.get("ok"):
        await robot_server._send_stop_now(reason=f"camera_{rep0.get('reason')}")
        return {"executed": False, "reason": f"camera_{rep0.get('reason')}", "image": rep0}

    # Decide whether we can use goal-mode (Isaac extension) or must use cmd_vel (IsaacLab/external).
    mode0 = str((st0.get("mode") or robot_server.humanoid_mode or "")).lower().strip()
    force_goal = str(os.getenv("GIL_STEER_FORCE_GOAL_MODE", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
    use_goal_mode = force_goal or (mode0 == "goal")
    await robot_server._send_backend_command(
        command_envelope(
            "walker_mode",
            robot_kind="humanoid",
            payload={"mode": "goal" if use_goal_mode else "external"},
        ),
        robot_kind="humanoid",
    )
    en = json.loads(await enable_humanoid_motion(reason="closed_loop_steer"))  # reuse tool logic
    if en.get("status") != "success":
        return {"executed": False, "reason": "enable_failed", "details": en}

    try:
        while (time.time() - t0) < float(max_s):
            robot_server.safety.heartbeat(source="closed_loop")
            obs = _make_live_observation_for_director()
            base = obs.get("base") or {}
            x = float(base.get("x", 0.0) or 0.0)
            y = float(base.get("y", 0.0) or 0.0)
            z = float(base.get("z", 0.0) or 0.0)
            # Gate on the currently best stream (not just the collage).
            st_live = robot_server._refresh_health("humanoid")
            img, rep = _pick_best_image(st_live)
            obs["image"] = img
            if not rep.get("ok"):
                await robot_server._send_stop_now(reason=f"camera_{rep.get('reason')}")
                await disable_humanoid_motion(reason=f"camera_{rep.get('reason')}")  # type: ignore[arg-type]
                return {"executed": False, "reason": f"camera_{rep.get('reason')}", "image": rep, "steps": steps}

            # Update belief/map.
            try:
                if d.robot_id in d.orch.maps:
                    d.orch.maps[d.robot_id].ingest(obs)
            except Exception:
                pass

            d_exit = float(math.hypot(x - goal_x, y - goal_y))
            if d_exit <= goal_r:
                await robot_server._send_stop_now(reason="goal_reached")
                await disable_humanoid_motion(reason="goal_reached")  # type: ignore[arg-type]
                return {
                    "executed": True,
                    "reason": "goal_reached",
                    "steps": steps,
                    "final": {"x": x, "y": y, "z": z, "dist_to_goal": d_exit},
                }

            path = maze.shortest_cell_path((x, y), (goal_x, goal_y))
            if not path or len(path) < 2:
                await robot_server._send_stop_now(reason="no_path")
                await disable_humanoid_motion(reason="no_path")  # type: ignore[arg-type]
                return {"executed": False, "reason": "no_path", "steps": steps, "dist_to_goal": d_exit}

            nxt = path[1]
            wx, wy = maze.cell_center(nxt[0], nxt[1])
            waypoint = (float(wx), float(wy))
            if use_goal_mode:
                if last_waypoint != waypoint:
                    await robot_server._send_backend_command(
                        command_envelope("set_goal", robot_kind="humanoid", payload={"x": float(wx), "y": float(wy)}),
                        robot_kind="humanoid",
                    )
                    last_waypoint = waypoint
            else:
                # External/cmd_vel mode: steer toward the next waypoint using a simple heading controller.
                yaw = float((base.get("yaw") or 0.0))
                dx = float(wx - x)
                dy = float(wy - y)
                dist = float(math.hypot(dx, dy))
                tgt = float(math.atan2(dy, dx))
                err = float((tgt - yaw + math.pi) % (2.0 * math.pi) - math.pi)
                wz_max = float(os.getenv("GIL_STEER_WZ_MAX", "0.45") or 0.45)
                vx_max = float(os.getenv("GIL_STEER_VX_MAX", "0.14") or 0.14)
                k_w = 1.6
                k_v = 0.8
                wz = float(max(-wz_max, min(wz_max, k_w * err)))
                # Turn-in-place when we're badly misaligned.
                turn_in_place_err = float(os.getenv("GIL_STEER_TURN_IN_PLACE_ERR", "0.75") or 0.75)
                if abs(err) > turn_in_place_err:
                    vx = 0.0
                else:
                    vx = float(min(vx_max, max(0.0, k_v * dist)))
                validation = robot_server.safety.validate_motion(
                    command_type="cmd_vel",
                    state=robot_server._refresh_health("humanoid"),
                    payload={"vx": vx, "vy": 0.0, "wz": wz, "duration_s": period},
                )
                if not validation.get("ok"):
                    await robot_server._send_stop_now(reason=str(validation.get("error") or "motion_rejected"))
                    await disable_humanoid_motion(reason="motion_rejected")  # type: ignore[arg-type]
                    return {"executed": False, "reason": "motion_rejected", "details": validation, "steps": steps}
                payload = validation["payload"]
                await robot_server._send_backend_command(
                    command_envelope(
                        "cmd_vel",
                        robot_kind="humanoid",
                        reason="closed_loop_cmd_vel",
                        payload={"vx": payload["vx"], "vy": payload.get("vy", 0.0), "wz": payload["wz"], "dt": period},
                    ),
                    robot_kind="humanoid",
                )

            steps += 1
            await asyncio.sleep(period)
    finally:
        try:
            await disable_humanoid_motion(reason="closed_loop_done")  # type: ignore[arg-type]
        except Exception:
            pass

    await robot_server._send_stop_now(reason="timeout")
    return {"executed": False, "reason": "timeout", "steps": steps}


@mcp.tool()
async def get_steering_status() -> str:
    """Stage machine snapshot: readiness ladder A, target skill, competence ledger, first gap."""
    robot_server.safety.heartbeat(source="mcp")
    snap = _agent_director().snapshot(live_facts=_live_facts())
    return json.dumps({"ok": True, "stage": snap.as_dict(), "live": _live_facts()})


@mcp.tool()
async def evaluate_skill(skill_id: str = "") -> str:
    """Dream-eval a curriculum skill and write pass/fail on the competence ledger."""
    robot_server.safety.heartbeat(source="mcp")
    return json.dumps(_agent_director().evaluate_skill(skill_id or None))


@mcp.tool()
async def learn_skill(skill_id: str = "") -> str:
    """Learn the first missing skill in imagination (no motors)."""
    robot_server.safety.heartbeat(source="mcp")
    return json.dumps(_agent_director().learn_skill(skill_id or None))


@mcp.tool()
async def list_embodiments() -> str:
    """List Isaac Sim robot bodies the agent can select (H1, G1, Franka, ...)."""
    return json.dumps(_agent_director().list_embodiments())


@mcp.tool()
async def select_embodiment(key: str, robot_id: str = "", token: str = "") -> str:
    """Select a catalog robot. Factory cameras/policy require relaunching Isaac with launch.command (launch_isaac_robot.ps1)."""
    result = _agent_director().select_embodiment(key, robot_id=robot_id or None, token=token or None)
    return json.dumps(result)


@mcp.tool()
async def ingest_world(source: str, content: str) -> str:
    """Create or record a world from text, image, video, or huggingface occupancy JSON."""
    result = _agent_director().ingest_world(source, content)
    cmd = result.get("isaac_command") or {}
    if cmd:
        await robot_server._send_backend_command(
            command_envelope("load_world", robot_kind="humanoid", payload=cmd),
            robot_kind="humanoid",
        )
    return json.dumps(result)


@mcp.tool()
async def instruct(instruction: str, x: float | None = None, y: float | None = None) -> str:
    """Tell the attached robot what it needs to be able to do."""
    return json.dumps(_agent_director().instruct(instruction, x=x, y=y))


@mcp.tool()
async def dream(n: int = 8) -> str:
    """Imagine rollouts in a copy of the Isaac scene. Does not move motors as execute."""
    return json.dumps(_agent_director().dream(n=n))


@mcp.tool()
async def preview_plan() -> str:
    """Replay the kept dream in Isaac as preview_vel (visible, not a gated commit)."""
    plan = _agent_director().preview_plan()
    if not plan.get("ok"):
        return json.dumps(plan)
    sent = 0
    robot_server.safety.heartbeat(source="preview_plan")
    for cmd in plan.get("commands") or []:
        validation = robot_server.safety.validate_motion(
            command_type="preview_vel",
            state=robot_server._refresh_health("humanoid"),
            payload={"vx": cmd.get("vx", 0.0), "vy": cmd.get("vy", 0.0), "wz": cmd.get("wz", 0.0), "duration_s": cmd.get("dt", 0.2)},
            source="orchestrator",
        )
        if not validation.get("ok"):
            return json.dumps({"status": "error", "error": validation.get("error"), "sent": sent})
        payload = validation["payload"]
        await robot_server._send_backend_command(
            command_envelope(
                "preview_vel",
                robot_kind="humanoid",
                reason="dream_preview",
                payload={"vx": payload["vx"], "vy": payload["vy"], "wz": payload["wz"], "dt": float(cmd.get("dt", 0.2))},
            ),
            robot_kind="humanoid",
        )
        sent += 1
        await asyncio.sleep(min(0.2, float(cmd.get("dt", 0.05))))
    await robot_server._send_backend_command(
        command_envelope("reset_episode", robot_kind="humanoid", payload={"x": -3.55, "y": -2.65, "yaw": 1.5707963267948966, "z": 1.05}),
        robot_kind="humanoid",
    )
    return json.dumps({"status": "success", "phase": "dream", "sent": sent, "reset": True})


@mcp.tool()
async def steer(
    instruction: str = "",
    embodiment: str = "unitree_h1",
    world_source: str = "text",
    world_content: str = "",
    commit: bool = False,
) -> str:
    """Agent process: readiness A, competence B or learn L, dream; execute only if gate and skill pass."""
    robot_server.safety.heartbeat(source="mcp")
    # NOTE: the director's internal `commit` path uses FakeControls. For live Isaac,
    # we execute motion here in a closed loop when commit=True.
    result = _agent_director().steer(
        instruction=instruction or None,
        embodiment=embodiment or None,
        world_source=world_source,
        world_content=world_content,
        commit=False,
        live_facts=_live_facts(),
    )
    isaac_cmds = []
    if _agent_director().embodiment:
        isaac_cmds.append({"type": "set_embodiment", "usd_rel": _agent_director().embodiment.usd_rel, "key": _agent_director().embodiment.key})
    if _agent_director().world:
        isaac_cmds.append(_agent_director().world.isaac_command)
    for cmd in isaac_cmds:
        await robot_server._send_backend_command(
            command_envelope(str(cmd.get("type") or "load_world"), robot_kind="humanoid", payload=cmd),
            robot_kind="humanoid",
        )

    if commit:
        dream = (result.get("dream") or {}) if isinstance(result, dict) else {}
        gate = (dream.get("gate") or {}) if isinstance(dream, dict) else {}
        if not bool(gate.get("ok")):
            result["note"] = "Refused to execute: gate blocked."
            return json.dumps(result)
        if str(result.get("decision") or "") not in ("execute", "ready"):
            result["note"] = "Refused to execute: not in execute state."
            return json.dumps(result)
        loop = await _closed_loop_commit(
            hz=float(os.getenv("GIL_STEER_LOOP_HZ", "8") or 8),
            max_s=float(os.getenv("GIL_STEER_MAX_S", "180") or 180),
        )
        result["executed"] = bool(loop.get("executed"))
        result["mission_reason"] = loop.get("reason")
        result["loop"] = loop
        result["note"] = "Closed-loop execution complete." if result["executed"] else "Closed-loop execution refused/stopped."
    return json.dumps(result)


@mcp.tool()
async def execute_action(
    action_kind: str,
    robot_kind: str = "",
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    vx: float = 0.0,
    vy: float = 0.0,
    wz: float = 0.0,
    duration_s: float = 0.25,
    gripper_open: bool = True,
    reason: str = "",
) -> str:
    """Generic robot action dispatcher. Replaces drive_humanoid / move_arm / control_gripper.

    action_kind: cmd_vel | move_robot | gripper | stop | preview_vel
    robot_kind: humanoid | arm (auto-detected if empty)
    """
    robot_server.safety.heartbeat(source="execute_action")
    kind = (robot_kind or "").strip().lower() or robot_server._choose_default_kind()
    if action_kind == "cmd_vel":
        validation = robot_server.safety.validate_motion(
            command_type="cmd_vel",
            state=robot_server._refresh_health("humanoid"),
            payload={"vx": vx, "vy": vy, "wz": wz, "duration_s": duration_s},
        )
        if not validation.get("ok"):
            return json.dumps({"status": "error", "error": validation.get("error")})
        payload = validation["payload"]
        await robot_server._send_backend_command(
            command_envelope("cmd_vel", robot_kind="humanoid", reason=reason,
                             payload={"vx": payload["vx"], "vy": payload["vy"], "wz": payload["wz"], "dt": 0.1}),
            robot_kind="humanoid",
        )
        return json.dumps({"status": "success", "action": "cmd_vel", "payload": payload})
    elif action_kind == "move_robot":
        validation = robot_server.safety.validate_motion(
            command_type="move_robot",
            state=robot_server._refresh_health("arm"),
            payload={"x": x, "y": y, "z": z},
        )
        if not validation.get("ok"):
            return json.dumps({"status": "error", "error": validation.get("error")})
        result = await robot_server._send_backend_command(
            command_envelope("move_robot", robot_kind="arm", reason=reason, payload=validation["payload"]),
            robot_kind="arm",
        )
        await asyncio.sleep(1.0)
        return json.dumps({"status": "success", "action": "move_robot", "target": validation["payload"], "result": result})
    elif action_kind == "gripper":
        result = await robot_server._send_backend_command(
            command_envelope("gripper", robot_kind="arm", payload={"open": gripper_open}),
            robot_kind="arm",
        )
        await asyncio.sleep(0.5)
        return json.dumps({"status": "success", "action": "gripper", "open": gripper_open, "result": result})
    elif action_kind == "stop":
        result = await robot_server._send_stop_now(reason=reason or "execute_action_stop")
        return json.dumps({"status": "success", "action": "stop", "result": result})
    else:
        return json.dumps({"status": "error", "error": f"Unknown action_kind '{action_kind}'"})


@mcp.tool()
async def get_observation(robot_kind: str = "") -> str:
    """Unified observation: state + image + objects for any robot kind."""
    kind = (robot_kind or "").strip().lower() or robot_server._choose_default_kind()
    state = robot_server._refresh_health(kind)
    return json.dumps({
        "morphology": kind,
        "state": sanitized_state(state),
        "image": state.get("last_image"),
        "images": state.get("images") or {},
        "objects": state.get("objects") or [],
    })


@mcp.tool()
async def get_metrics() -> str:
    """Return internal GIL metrics (counters, gauges)."""
    try:
        from gil.core.metrics import METRICS
        return json.dumps(METRICS.snapshot())
    except Exception:
        return json.dumps({"counters": {}, "gauges": {}})


def create_http_app() -> FastAPI:
    """ASGI app factory (MCP + inspector REST for browser tooling)."""
    app = FastAPI(title="GIL Controls", version="1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_inspector_allowed_origins(),
        allow_origin_regex=_inspector_allow_origin_regex(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )

    @app.get("/api/inspector")
    async def api_inspector(robot_kind: str = "") -> dict[str, Any]:
        return _build_inspector_snapshot(robot_kind)

    @app.post("/api/steer")
    async def api_steer(payload: dict[str, Any]) -> dict[str, Any]:
        # Thin REST wrapper over the existing MCP tool. Still safety-gated.
        instruction = str(payload.get("instruction") or "")
        embodiment = str(payload.get("embodiment") or "unitree_h1")
        world_source = str(payload.get("world_source") or "text")
        world_content = str(payload.get("world_content") or "")
        commit = bool(payload.get("commit"))
        txt = await steer(
            instruction=instruction,
            embodiment=embodiment,
            world_source=world_source,
            world_content=world_content,
            commit=commit,
        )
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": False, "error": "steer_non_json", "raw": txt}

    @app.post("/api/stop")
    async def api_stop(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reason = ""
        if isinstance(payload, dict):
            reason = str(payload.get("reason") or "")
        txt = await stop_humanoid_now(reason=reason)
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": False, "error": "stop_non_json", "raw": txt}

    @app.post("/api/reset")
    async def api_reset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        args = payload if isinstance(payload, dict) else {}
        txt = await reset_humanoid_episode(
            x=args.get("x"),
            y=args.get("y"),
            yaw=args.get("yaw"),
            z=args.get("z"),
        )
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": False, "error": "reset_non_json", "raw": txt}

    # Lightweight REST control endpoints for demos/scripts that don't want to depend on
    # long-lived MCP StreamableHTTP sessions.
    @app.post("/api/humanoid/mode")
    async def api_humanoid_mode(payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "").strip().lower()
        if mode not in ("goal", "external"):
            return {"ok": False, "error": "mode must be 'goal' or 'external'"}
        txt = await set_humanoid_mode(mode=mode)
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": True, "raw": txt}

    @app.post("/api/humanoid/goal")
    async def api_humanoid_goal(payload: dict[str, Any]) -> dict[str, Any]:
        x = float(payload.get("x"))
        y = float(payload.get("y"))
        txt = await set_humanoid_goal(x=x, y=y)
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": True, "raw": txt}

    @app.post("/api/humanoid/motion/enable")
    async def api_humanoid_motion_enable(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        args = payload if isinstance(payload, dict) else {}
        reason = str(args.get("reason") or "")
        txt = await enable_humanoid_motion(reason=reason)
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": True, "raw": txt}

    @app.post("/api/humanoid/motion/disable")
    async def api_humanoid_motion_disable(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        args = payload if isinstance(payload, dict) else {}
        reason = str(args.get("reason") or "")
        txt = await disable_humanoid_motion(reason=reason)
        try:
            return json.loads(txt)
        except Exception:
            return {"ok": True, "raw": txt}

    @app.post("/api/spawn_world")
    async def api_spawn_world(payload: dict[str, Any]) -> dict[str, Any]:
        """Spawn/teleport the robot by setting a world anchor and resetting local pose.

        This is an *inspection/demo* affordance. For real robotics, spawning should
        be done by the backend (sim/hardware), while `gil_controls` remains the
        contract boundary.
        """
        kind = str(payload.get("robot_kind") or "humanoid").strip().lower() or "humanoid"
        if kind not in ("humanoid", "arm"):
            kind = "humanoid"

        lon = float(payload.get("lon"))
        lat = float(payload.get("lat"))
        height = float(payload.get("height") or 0.0)
        yaw = float(payload.get("yaw") or 0.0)
        z = float(payload.get("z") or 1.05)

        # Persist anchor into robot state (source of truth for UI).
        st = robot_server.state_by_kind.setdefault(kind, default_robot_state(kind))
        st["world_anchor"] = {"lon": lon, "lat": lat, "height": height, "source": "spawn_world", "at_ms": int(time.time() * 1000)}
        try:
            robot_server._merge_update(kind, {"world_anchor": st["world_anchor"]}, source="api:spawn_world")
        except Exception:
            pass

        # Reset local pose at the anchor. (x/y are local ENU meters)
        if kind == "humanoid":
            txt = await reset_humanoid_episode(x=0.0, y=0.0, yaw=yaw, z=z)
            try:
                res = json.loads(txt)
            except Exception:
                res = {"ok": False, "error": "reset_non_json", "raw": txt}
        else:
            # Arm spawn is a no-op for now; anchor still helps visualization.
            res = {"ok": True, "note": "arm_spawn_anchor_only"}

        return {"ok": True, "robot_kind": kind, "anchor": st["world_anchor"], "reset": res}

    @app.post("/api/drive_distance")
    async def api_drive_distance(payload: dict[str, Any]) -> dict[str, Any]:
        """Drive the humanoid forward for a target distance in meters (best-effort).

        This is a convenience endpoint for demos/teleop; it still respects the safety supervisor limits.
        """
        distance_m = float(payload.get("distance_m") or 0.0)
        vx = float(payload.get("vx") or 0.45)
        if not (distance_m > 0.0):
            return {"ok": False, "error": "distance_m must be > 0"}
        if abs(vx) < 1e-3:
            return {"ok": False, "error": "vx must be non-zero"}

        # Ensure mode + motion.
        try:
            await set_humanoid_mode(mode="external")
        except Exception:
            pass
        en = json.loads(await enable_humanoid_motion(reason="drive_distance"))
        if en.get("status") != "success":
            return {"ok": False, "error": "enable_motion_failed", "details": en}

        # Segment to satisfy max_drive_duration_s.
        max_seg_s = float(getattr(robot_server.safety.cfg, "max_drive_duration_s", 10.0) or 10.0)
        max_seg_s = max(0.25, min(10.0, max_seg_s))
        # Keep segments short so browser/tool calls don't hang for minutes.
        seg_s = min(3.5, max_seg_s * 0.95)

        st0 = robot_server._refresh_health("humanoid")
        b0 = (st0.get("base") or {}) if isinstance(st0, dict) else {}
        x0, y0 = float(b0.get("x", 0.0) or 0.0), float(b0.get("y", 0.0) or 0.0)

        target = float(distance_m)
        moved = 0.0
        steps = 0
        t0 = time.time()
        while moved < target and time.time() - t0 < 180.0:
            remaining = target - moved
            want_s = min(seg_s, remaining / max(abs(vx), 1e-6))
            await drive_humanoid(vx=float(vx), vy=0.0, wz=0.0, duration_s=float(want_s), reason="drive_distance")
            await asyncio.sleep(0.05)
            st = robot_server._refresh_health("humanoid")
            b = (st.get("base") or {}) if isinstance(st, dict) else {}
            x, y = float(b.get("x", 0.0) or 0.0), float(b.get("y", 0.0) or 0.0)
            moved = float(math.hypot(x - x0, y - y0))
            steps += 1
            # If the backend isn't updating pose, stop early.
            if steps >= 2 and moved < 0.02:
                break

        await disable_humanoid_motion(reason="drive_distance_done")
        return {"ok": True, "requested_m": target, "moved_m": moved, "steps": steps, "vx": vx}

    @app.get("/api/record/status")
    async def api_record_status() -> dict[str, Any]:
        return robot_server.recorder.status().__dict__

    @app.post("/api/record/start")
    async def api_record_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        args = payload if isinstance(payload, dict) else {}
        kind = str(args.get("robot_kind") or "humanoid")
        run_id = args.get("run_id")
        return robot_server.recorder.start(robot_kind=kind, run_id=(str(run_id) if run_id else None)).__dict__

    @app.post("/api/record/stop")
    async def api_record_stop() -> dict[str, Any]:
        return robot_server.recorder.stop().__dict__

    # Mount MCP last so explicit /api/* routes win.
    #
    # IMPORTANT: StreamableHTTP requires the session manager task-group to be started
    # via `session_manager.run()` (lifespan). When embedding inside FastAPI, wire it
    # to startup/shutdown handlers.
    mcp_http_app = mcp.streamable_http_app()
    app.mount("/", mcp_http_app)

    @app.on_event("startup")
    async def _mcp_streamable_http_startup() -> None:
        if getattr(app.state, "_mcp_streamable_http_cm", None) is not None:
            return
        cm = mcp.session_manager.run()
        await cm.__aenter__()
        app.state._mcp_streamable_http_cm = cm

    @app.on_event("shutdown")
    async def _mcp_streamable_http_shutdown() -> None:
        cm = getattr(app.state, "_mcp_streamable_http_cm", None)
        if cm is None:
            return
        try:
            await cm.__aexit__(None, None, None)
        finally:
            app.state._mcp_streamable_http_cm = None
    return app


async def main():
    import uvicorn

    server_task = asyncio.create_task(robot_server.start_server())
    watchdog_task = asyncio.create_task(robot_server.watchdog_loop())
    print("[CONTROLS] Starting MCP Server (Streamable HTTP) on Port 6769...")
    config = uvicorn.Config(create_http_app, host="0.0.0.0", port=6769, factory=True)
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        server_task.cancel()
        watchdog_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
