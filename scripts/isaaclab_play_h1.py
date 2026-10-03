#!/usr/bin/env python3
"""Play an Isaac Lab H1 locomotion (or maze overlay) env with GIL cmd_vel + WebRTC."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import gymnasium as gym
import torch


def _bootstrap() -> None:
    repo = Path(__file__).resolve().parents[1]
    # Always add the repo `src/` first (local task registrations, helpers, etc.).
    src = repo / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))

    # Prefer an existing IsaacLab install if available (e.g., E:\GIL\IsaacLab or pip installs).
    # Only fall back to the vendored `third_party/IsaacLab` when explicitly requested or if imports fail.
    use_vendored = str(os.environ.get("GIL_USE_VENDORED_ISAACLAB", "0") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not use_vendored:
        try:
            import isaaclab  # noqa: F401

            return
        except Exception:
            pass

    # Workstation/dev installs may vendor IsaacLab under third_party/.
    isaaclab_source = repo / "third_party" / "IsaacLab" / "source"
    for pkg_root in (
        "isaaclab",
        "isaaclab_assets",
        "isaaclab_tasks",
        "isaaclab_rl",
        "isaaclab_physx",
        "isaaclab_ovphysx",
        "isaaclab_newton",
        "isaaclab_contrib",
        "isaaclab_teleop",
        "isaaclab_visualizers",
    ):
        p = isaaclab_source / pkg_root
        if p.is_dir():
            sys.path.insert(0, str(p))

    for extra in (
        Path("/opt/IsaacLab/source/isaaclab"),
        Path("/opt/IsaacLab/source/isaaclab_tasks"),
        Path("/opt/IsaacLab/source/isaaclab_assets"),
        Path("/opt/IsaacLab/source/isaaclab_rl"),
    ):
        if extra.is_dir():
            sys.path.insert(0, str(extra))


_bootstrap()

from isaaclab.app import AppLauncher  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--task", type=str, default="Isaac-Velocity-Flat-H1-Maze-v0")
parser.add_argument("--num_envs", type=int, default=1)
parser.add_argument("--vx", type=float, default=0.0)
parser.add_argument("--wz", type=float, default=0.0)
parser.add_argument("--use_pretrained", action="store_true", default=os.environ.get("GIL_USE_POLICY", "1") != "0")
parser.add_argument("--checkpoint", type=str, default=os.environ.get("GIL_POLICY_CHECKPOINT", ""))
AppLauncher.add_app_launcher_args(parser)
# Workstation (Windows): native window. Docker WSL: headless (no Vulkan).
_livestream = int(os.environ.get("GIL_LIVESTREAM", "0") or "0")
_headless_default = os.environ.get("GIL_HEADLESS")
if _headless_default is None:
    _headless_default = "1" if os.name != "nt" else "0"
parser.set_defaults(
    enable_cameras=False,
    headless=str(_headless_default).strip() not in ("0", "false", "False"),
    livestream=_livestream,
)
args = parser.parse_args()
if os.environ.get("GIL_USE_POLICY", "1") in ("0", "false", "False"):
    args.use_pretrained = False
if int(getattr(args, "livestream", 0) or 0):
    args.headless = True
    setattr(args, "enable_cameras", True)
print(
    f"[GIL] launcher_args headless={getattr(args,'headless',None)} enable_cameras={getattr(args,'enable_cameras',None)} "
    f"livestream={getattr(args,'livestream',None)} viz={getattr(args,'visualizer',None)} experience={getattr(args,'experience',None)!r}",
    flush=True,
)

def _encode_rgb_jpeg(rgb_u8_hwc, *, quality: int = 70) -> bytes:
    """Encode a single HxWx3 RGB image to JPEG bytes (CPU).

    Accepts uint8 in [0,255] or float in [0,1]/[0,255] and coerces to uint8.
    JPEG is intentionally used to keep websocket payload sizes small.
    """
    try:
        from PIL import Image  # pillow is pinned in the Isaac Sim env
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"PIL not available for JPEG encode: {exc!r}") from exc
    import numpy as np

    arr = rgb_u8_hwc
    if hasattr(arr, "detach"):
        arr = arr.detach()
    if hasattr(arr, "cpu"):
        arr = arr.cpu()
    if hasattr(arr, "numpy"):
        arr = arr.numpy()
    arr = np.asarray(arr)
    # Support RGBA by dropping alpha.
    if arr.ndim == 3 and arr.shape[-1] == 4:
        arr = arr[..., :3]
    if arr.ndim != 3 or arr.shape[-1] != 3:
        raise ValueError(f"Expected HxWx3 uint8, got shape={arr.shape!r}")
    if arr.dtype != np.uint8:
        # Heuristic conversion: float images are commonly in [0,1].
        if np.issubdtype(arr.dtype, np.floating):
            mx = float(np.nanmax(arr)) if arr.size else 0.0
            if mx <= 1.5:
                arr = arr * 255.0
        arr = np.clip(arr, 0.0, 255.0).astype(np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    buf = BytesIO()
    q = int(quality)
    q = 10 if q < 10 else (95 if q > 95 else q)
    img.save(buf, format="JPEG", quality=q, optimize=True)
    return buf.getvalue()


class GilBridge:
    def __init__(self, url: str, vx: float, wz: float, *, kind: str = "humanoid") -> None:
        self.url = url
        self.kind = (kind or "humanoid").strip().lower()
        if self.kind not in ("humanoid", "arm"):
            self.kind = "humanoid"
        self.vx = float(vx)
        self.vy = 0.0
        self.wz = float(wz)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._base = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
        self._arm_state = {
            "end_effector": {"x": 0.0, "y": 0.0, "z": 0.0},
            "joints": {},
            "gripper_open": True,
        }
        self._arm_target = {"x": 0.0, "y": 0.3, "z": 0.0}  # NOTE: command coords (y-up)
        self._arm_gripper_open_cmd = True
        self._image_data_url = ""
        self._image_wide_data_url = ""
        self._extra_images: dict[str, str] = {}
        self._reset_requested = False

    def start(self) -> None:
        if not self.url:
            return
        threading.Thread(target=self._run, name="gil-ws", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def set_base(self, x: float, y: float, z: float, yaw: float) -> None:
        with self._lock:
            self._base = {"x": float(x), "y": float(y), "z": float(z), "yaw": float(yaw)}

    def set_image_jpeg(self, jpg: bytes) -> None:
        url = "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")
        with self._lock:
            self._image_data_url = url

    def set_images_jpeg(self, *, fpv_jpg: bytes | None = None, wide_jpg: bytes | None = None) -> None:
        with self._lock:
            if fpv_jpg:
                self._image_data_url = "data:image/jpeg;base64," + base64.b64encode(fpv_jpg).decode("ascii")
            if wide_jpg:
                self._image_wide_data_url = "data:image/jpeg;base64," + base64.b64encode(wide_jpg).decode("ascii")

    def set_extra_png(self, name: str, png: bytes) -> None:
        nm = str(name or "").strip() or "extra"
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        with self._lock:
            self._extra_images[nm] = url

    def vel(self) -> tuple[float, float, float]:
        with self._lock:
            return self.vx, self.vy, self.wz

    def set_arm_state(self, *, end_effector: dict, joints: dict, gripper_open: bool) -> None:
        with self._lock:
            self._arm_state = {
                "end_effector": dict(end_effector or {}),
                "joints": dict(joints or {}),
                "gripper_open": bool(gripper_open),
            }

    def arm_command(self) -> tuple[dict, bool]:
        """Return (target_xyz_cmd_coords, gripper_open_cmd)."""
        with self._lock:
            return dict(self._arm_target), bool(self._arm_gripper_open_cmd)

    def request_reset(self) -> None:
        with self._lock:
            self._reset_requested = True

    def consume_reset(self) -> bool:
        with self._lock:
            if self._reset_requested:
                self._reset_requested = False
                return True
            return False

    def _run(self) -> None:
        try:
            asyncio.run(self._loop())
        except Exception as exc:
            print(f"[GIL][ws] bridge exited: {exc!r}", flush=True)

    async def _loop(self) -> None:
        import websockets

        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"type": "hello", "kind": self.kind}))
                    print(f"[GIL][ws] connected {self.url}", flush=True)
                    last = 0.0
                    last_img = 0.0
                    while not self._stop.is_set():
                        now = time.time()
                        if now - last >= 0.05:
                            with self._lock:
                                if self.kind == "arm":
                                    payload = {
                                        "type": "scene_state",
                                        "robot_kind": "arm",
                                        "end_effector": dict(self._arm_state.get("end_effector") or {}),
                                        "joints": dict(self._arm_state.get("joints") or {}),
                                        "gripper_open": bool(self._arm_state.get("gripper_open", True)),
                                        "mode": "arm",
                                    }
                                else:
                                    payload = {
                                        "type": "scene_state",
                                        "robot_kind": "humanoid",
                                        "base": dict(self._base),
                                        "mode": "external",
                                    }
                            await ws.send(json.dumps(payload))
                            last = now
                        if now - last_img >= 0.25:
                            with self._lock:
                                img = self._image_data_url
                                img_wide = self._image_wide_data_url
                                extras = dict(self._extra_images)
                                base = dict(self._base)
                            if img:
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "scene_image",
                                            "robot_kind": "arm" if self.kind == "arm" else "humanoid",
                                            "image": img,
                                            "image_wide": img_wide,
                                            "images": extras,
                                            "base": base,
                                        }
                                    )
                                )
                            last_img = now
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.02)
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            break
                        try:
                            cmd = json.loads(raw)
                        except Exception:
                            continue
                        t = str(cmd.get("type") or "")
                        if t in ("cmd_vel", "preview_vel"):
                            with self._lock:
                                self.vx = float(cmd.get("vx", 0.0) or 0.0)
                                self.vy = float(cmd.get("vy", 0.0) or 0.0)
                                self.wz = float(cmd.get("wz", 0.0) or 0.0)
                        elif t in ("stop", "stop_now", "estop"):
                            with self._lock:
                                self.vx = self.vy = self.wz = 0.0
                        elif t in ("reset_episode", "reset"):
                            # Controls asked us to reset the simulator episode.
                            with self._lock:
                                self.vx = self.vy = self.wz = 0.0
                                self._reset_requested = True
                        elif t == "move_robot":
                            if (cmd.get("robot_kind") or "arm") == "arm":
                                with self._lock:
                                    # NOTE: arm command coords (x, y-up, z)
                                    self._arm_target = {
                                        "x": float(cmd.get("x", self._arm_target["x"]) or 0.0),
                                        "y": float(cmd.get("y", self._arm_target["y"]) or 0.0),
                                        "z": float(cmd.get("z", self._arm_target["z"]) or 0.0),
                                    }
                        elif t == "gripper":
                            if (cmd.get("robot_kind") or "arm") == "arm":
                                with self._lock:
                                    self._arm_gripper_open_cmd = bool(cmd.get("open", True))
            except Exception as exc:
                print(f"[GIL][ws] reconnect in 1s: {exc!r}", flush=True)
                await asyncio.sleep(1.0)


class _LiveMap:
    """Top-down maze map (no Kit RTX). Served at GIL_VIZ_PORT (default 8211)."""

    def __init__(self, maze, size: int = 640) -> None:
        self.maze = maze
        self.size = int(size)
        spec = maze.spec
        pad = 0.6
        self.xmin = float(spec.origin_x) - pad
        self.ymin = float(spec.origin_y) - pad
        self.xmax = float(spec.origin_x) + float(spec.width) * float(spec.cell_size) + pad
        self.ymax = float(spec.origin_y) + float(spec.height) * float(spec.cell_size) + pad
        self._png = b""
        self._lock = threading.Lock()

    def world_to_px(self, x: float, y: float) -> tuple[int, int]:
        u = (x - self.xmin) / max(1e-6, self.xmax - self.xmin)
        v = (y - self.ymin) / max(1e-6, self.ymax - self.ymin)
        px = int(u * (self.size - 1))
        py = int((1.0 - v) * (self.size - 1))
        return px, py

    def render(self, x: float, y: float, yaw: float, vx: float, vy: float, wz: float) -> bytes:
        w = h = self.size
        buf = bytearray(w * h * 3)
        floor = (24, 28, 36)
        for i in range(0, len(buf), 3):
            buf[i : i + 3] = floor
        for wall in self.maze.walls:
            self._fill_box(buf, w, h, wall.cx, wall.cy, wall.sx, wall.sy, (180, 180, 190))
        sx, sy, _ = self.maze.start
        gx, gy, _ = self.maze.goal
        self._fill_disk(buf, w, h, sx, sy, 0.18, (40, 180, 90))
        self._fill_disk(buf, w, h, gx, gy, 0.22, (210, 70, 70))
        self._draw_robot(buf, w, h, x, y, yaw, vx, vy)
        png = _encode_png(w, h, bytes(buf))
        with self._lock:
            self._png = png
        return png

    def png(self) -> bytes:
        with self._lock:
            return self._png

    def _fill_box(self, buf, w, h, cx, cy, sx, sy, rgb) -> None:
        x0, y0 = self.world_to_px(cx - sx * 0.5, cy + sy * 0.5)
        x1, y1 = self.world_to_px(cx + sx * 0.5, cy - sy * 0.5)
        xa, xb = max(0, min(x0, x1)), min(w - 1, max(x0, x1))
        ya, yb = max(0, min(y0, y1)), min(h - 1, max(y0, y1))
        r, g, b = rgb
        for py in range(ya, yb + 1):
            row = py * w * 3
            for px in range(xa, xb + 1):
                i = row + px * 3
                buf[i] = r
                buf[i + 1] = g
                buf[i + 2] = b

    def _fill_disk(self, buf, w, h, cx, cy, radius, rgb) -> None:
        px, py = self.world_to_px(cx, cy)
        pr = max(3, int(radius / max(1e-6, self.xmax - self.xmin) * self.size))
        r, g, b = rgb
        for dy in range(-pr, pr + 1):
            yy = py + dy
            if yy < 0 or yy >= h:
                continue
            row = yy * w * 3
            for dx in range(-pr, pr + 1):
                if dx * dx + dy * dy > pr * pr:
                    continue
                xx = px + dx
                if xx < 0 or xx >= w:
                    continue
                i = row + xx * 3
                buf[i] = r
                buf[i + 1] = g
                buf[i + 2] = b

    def _draw_robot(self, buf, w, h, x, y, yaw, vx, vy) -> None:
        self._fill_disk(buf, w, h, x, y, 0.16, (80, 200, 255))
        fx = x + 0.35 * math.cos(yaw)
        fy = y + 0.35 * math.sin(yaw)
        self._fill_disk(buf, w, h, fx, fy, 0.08, (255, 220, 80))
        if abs(vx) + abs(vy) > 1e-3:
            self._fill_disk(buf, w, h, x + 0.5 * vx, y + 0.5 * vy, 0.06, (120, 255, 160))


def _encode_png(width: int, height: int, rgb: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _start_map_http(live: _LiveMap, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/map.png", "/map"):
                body = live.png() or b""
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            html = (
                "<!doctype html><meta charset=utf-8><title>GIL maze (container)</title>"
                "<body style='margin:0;background:#111;color:#eee;font:14px sans-serif'>"
                "<p style='margin:8px'>Live top-down maze (physics). WebRTC is unavailable in this Docker GPU setup.</p>"
                "<img id=m style='width:min(90vw,720px);image-rendering:pixelated'>"
                "<script>setInterval(()=>{m.src='/map.png?t='+Date.now()},200)</script>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

    httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    threading.Thread(target=httpd.serve_forever, name="gil-map-http", daemon=True).start()
    print(f"[GIL] live maze map http://127.0.0.1:{port}/  (containerized, no RTX)", flush=True)


def _pretrained_task_for(task: str) -> str:
    if "H1-Maze" in task or task.endswith("H1-Maze-v0"):
        return "Isaac-Velocity-Flat-H1-v0"
    return task


def _try_load_rsl_policy(env, task: str, checkpoint: str):
    """Load NVIDIA published RSL-RL policy so GIL cmd_vel actually walks the body."""
    ckpt_task = _pretrained_task_for(task)
    path = (checkpoint or "").strip()
    try:
        from isaaclab_tasks.utils.parse_cfg import load_cfg_from_registry
        from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper
        from rsl_rl.runners import OnPolicyRunner
    except Exception as exc:
        print(f"[GIL] RSL-RL stack not available ({exc!r}); zero joint actions", flush=True)
        return None, env
    if not path:
        try:
            from isaaclab.utils.pretrained_checkpoint import get_published_pretrained_checkpoint

            path = get_published_pretrained_checkpoint("rsl_rl", ckpt_task) or ""
        except Exception as exc:
            print(f"[GIL] published checkpoint lookup failed ({exc!r})", flush=True)
            path = ""
    if not path:
        print(f"[GIL] no pretrained policy for {ckpt_task}; standing with zero actions", flush=True)
        return None, env
    try:
        agent_cfg = load_cfg_from_registry(ckpt_task, "rsl_rl_cfg_entry_point")
        wrapped = RslRlVecEnvWrapper(env)
        ppo_cfg = agent_cfg.to_dict() if hasattr(agent_cfg, "to_dict") else dict(agent_cfg)
        runner = OnPolicyRunner(wrapped, ppo_cfg, log_dir=None, device=str(env.unwrapped.device))
        runner.load(path)
        policy = runner.get_inference_policy(device=env.unwrapped.device)
        print(f"[GIL] loaded RSL-RL policy {path} (task={ckpt_task})", flush=True)
        return policy, wrapped
    except Exception as exc:
        import traceback

        print(f"[GIL] policy load failed ({exc!r}); zero joint actions", flush=True)
        try:
            traceback.print_exc()
        except Exception:
            pass
        return None, env


def _apply_velocity_command(env, vx: float, vy: float, wz: float) -> None:
    inner = env.unwrapped
    cmd_mgr = getattr(inner, "command_manager", None)
    if cmd_mgr is None:
        return
    try:
        term = cmd_mgr.get_term("base_velocity")
        cmd = term.command
        cmd[:, 0] = float(vx)
        cmd[:, 1] = float(vy)
        cmd[:, 2] = float(wz)
    except Exception:
        return


def main() -> None:
    app = AppLauncher(args).app
    try:
        import gil_isaaclab_tasks  # noqa: F401
    except Exception as exc:
        print(f"[GIL] local tasks import skipped: {exc!r}", flush=True)
    try:
        import isaaclab_tasks  # noqa: F401
    except Exception as exc:
        print(f"[GIL] isaaclab_tasks import skipped: {exc!r}", flush=True)

    from isaaclab.envs import ManagerBasedRLEnv

    env_cfg = None
    try:
        spec = gym.spec(args.task)
        entry = (spec.kwargs or {}).get("env_cfg_entry_point")
        print(f"[GIL] gym spec kwargs={spec.kwargs!r}", flush=True)
        if entry:
            # NVIDIA tasks use two styles:
            # - "module.path:CfgClass" (string)
            # - CfgClass (callable/class object)
            if isinstance(entry, str) and ":" in entry:
                mod_name, cls_name = entry.split(":", 1)
                import importlib

                cfg_cls = getattr(importlib.import_module(mod_name), cls_name)
                env_cfg = cfg_cls()
            elif callable(entry):
                env_cfg = entry()
            if hasattr(env_cfg, "scene") and hasattr(env_cfg.scene, "num_envs"):
                env_cfg.scene.num_envs = int(args.num_envs)
            print(f"[GIL] loaded env cfg {entry} type={type(env_cfg)!r}", flush=True)
    except Exception as exc:
        print(f"[GIL] cfg load failed: {exc!r}", flush=True)
        env_cfg = None
    if env_cfg is None:
        raise RuntimeError(f"Could not load Isaac Lab cfg for task {args.task}")

    task_lower = str(args.task or "").lower()
    kind = "arm" if "franka" in task_lower else "humanoid"
    if kind == "arm":
        # Keep the scene deterministic for interactive steering demos.
        try:
            ev = getattr(env_cfg, "events", None)
            if ev is not None:
                for name in (
                    "randomize_cube_positions",
                    "randomize_franka_joint_state",
                    "randomize_light",
                    "randomize_table_visual_material",
                    "randomize_robot_arm_visual_texture",
                ):
                    if hasattr(ev, name):
                        setattr(ev, name, None)
        except Exception:
            pass
    print("[GIL] constructing ManagerBasedRLEnv...", flush=True)
    env = ManagerBasedRLEnv(cfg=env_cfg)
    policy = None
    play_env = env
    if getattr(args, "use_pretrained", True):
        policy, play_env = _try_load_rsl_policy(env, args.task, getattr(args, "checkpoint", "") or "")
    print("[GIL] env constructed; resetting...", flush=True)
    obs, _ = play_env.reset()
    actions = torch.zeros(env.action_space.shape, device=env.unwrapped.device)
    print(f"[GIL] playing task={args.task} action_shape={tuple(actions.shape)} policy={'on' if policy else 'off'}", flush=True)

    # Quick shape sanity before stepping (helps diagnose IsaacLab installs where root_quat tensors are malformed).
    try:
        asset0 = play_env.scene["robot"]
        rpose = getattr(getattr(asset0, "data", None), "root_link_pose_w", None)
        if rpose is not None:
            try:
                wd = getattr(getattr(rpose, "warp", None), "dtype", None)
                ws = getattr(getattr(rpose, "warp", None), "shape", None)
                print(f"[GIL][dbg] root_link_pose_w.warp dtype={wd} shape={ws}", flush=True)
            except Exception:
                pass
            try:
                rt = getattr(rpose, "torch", None)
                if rt is not None:
                    print(f"[GIL][dbg] root_link_pose_w.torch shape={tuple(rt.shape)}", flush=True)
            except Exception:
                pass
        rq = getattr(getattr(getattr(asset0, "data", None), "root_quat_w", None), "torch", None)
        rv = getattr(getattr(getattr(asset0, "data", None), "root_lin_vel_w", None), "torch", None)
        if rq is not None:
            print(f"[GIL][dbg] root_quat_w.torch shape={tuple(rq.shape)}", flush=True)
        if rv is not None:
            print(f"[GIL][dbg] root_lin_vel_w.torch shape={tuple(rv.shape)}", flush=True)
    except Exception:
        pass

    live_map = None
    try:
        from gil_isaaclab_tasks.manager_based.maze_escape.config.h1.maze_env_cfg import _MAZE

        live_map = _LiveMap(_MAZE)
        _start_map_http(live_map, int(os.environ.get("GIL_VIZ_PORT", "8211") or "8211"))
    except Exception as exc:
        print(f"[GIL] live map skipped: {exc!r}", flush=True)

    bridge = GilBridge(os.environ.get("GIL_CONTROLS_WS", "").strip(), args.vx, args.wz, kind=kind)
    bridge.start()
    last_map = 0.0
    last_cam_stream = 0.0
    dbg_cam = str(os.environ.get("GIL_DEBUG_CAM", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
    dbg_cam_printed = False
    dbg_cam_missing: set[str] = set()

    def _maybe_stream_scene_camera() -> None:
        """Best-effort: stream IsaacLab camera sensors (fpv/chase/table/wrist) into gil_controls."""
        try:
            scene = getattr(env.unwrapped, "scene", None)
            if scene is None:
                return
            try:
                scene_keys = set(scene.keys()) if hasattr(scene, "keys") else set()
            except Exception:
                scene_keys = set()
            nonlocal dbg_cam_printed
            if dbg_cam and (not dbg_cam_printed):
                dbg_cam_printed = True
                try:
                    print(f"[GIL][cam] scene keys: {sorted(scene_keys)[:50]}", flush=True)
                except Exception:
                    pass
                for nm in ("fpv_cam", "chase_cam", "table_cam", "wrist_cam"):
                    try:
                        if scene_keys and nm not in scene_keys:
                            continue
                        cam0 = scene[nm]
                        data0 = getattr(cam0, "data", None)
                        out0 = getattr(data0, "output", None) if data0 is not None else None
                        out_keys = list(out0.keys()) if isinstance(out0, dict) else []
                        print(f"[GIL][cam] {nm}: type={type(cam0)} data={type(data0)} out_keys={out_keys}", flush=True)
                    except Exception:
                        continue
            if kind == "humanoid":
                fpv_png = None
                wide_png = None
                for cam_name in ("fpv_cam", "chase_cam"):
                    if scene_keys and cam_name not in scene_keys:
                        continue
                    try:
                        cam = scene[cam_name]
                    except Exception:
                        continue
                    # Some installs require an explicit sensor update before data is populated.
                    try:
                        upd = getattr(cam, "update", None)
                        if callable(upd):
                            upd(0.0)
                    except Exception:
                        pass
                    data = getattr(cam, "data", None)
                    rgb = None
                    if data is not None:
                        out = getattr(data, "output", None)
                        if isinstance(out, dict) and "rgb" in out:
                            rgb = out.get("rgb")
                        elif hasattr(data, "rgb"):
                            rgb = getattr(data, "rgb")
                    if rgb is None:
                        if dbg_cam and cam_name not in dbg_cam_missing:
                            dbg_cam_missing.add(cam_name)
                            try:
                                out = getattr(getattr(cam, "data", None), "output", None)
                                out_keys = list(out.keys()) if isinstance(out, dict) else []
                                print(f"[GIL][cam] {cam_name}: missing rgb (out_keys={out_keys})", flush=True)
                            except Exception:
                                pass
                        continue
                    rgb0 = rgb[0] if hasattr(rgb, "shape") and len(getattr(rgb, "shape")) == 4 else rgb
                    png = _encode_rgb_jpeg(rgb0, quality=int(os.environ.get("GIL_CAM_JPEG_QUALITY", "70") or "70"))
                    if cam_name == "fpv_cam":
                        fpv_png = png
                    else:
                        wide_png = png
                if fpv_png or wide_png:
                    bridge.set_images_jpeg(fpv_jpg=fpv_png, wide_jpg=wide_png)
                return
            # Arm tasks: keep the previous behavior (first available camera).
            for cam_name in ("table_cam", "wrist_cam"):
                if scene_keys and cam_name not in scene_keys:
                    continue
                try:
                    cam = scene[cam_name]
                except Exception:
                    continue
                try:
                    upd = getattr(cam, "update", None)
                    if callable(upd):
                        upd(0.0)
                except Exception:
                    pass
                data = getattr(cam, "data", None)
                rgb = None
                if data is not None:
                    out = getattr(data, "output", None)
                    if isinstance(out, dict) and "rgb" in out:
                        rgb = out.get("rgb")
                    elif hasattr(data, "rgb"):
                        rgb = getattr(data, "rgb")
                if rgb is None:
                    continue
                rgb0 = rgb[0] if hasattr(rgb, "shape") and len(getattr(rgb, "shape")) == 4 else rgb
                jpg = _encode_rgb_jpeg(rgb0, quality=int(os.environ.get("GIL_CAM_JPEG_QUALITY", "70") or "70"))
                bridge.set_image_jpeg(jpg)
                return
        except Exception:
            return

    try:
        # NOTE: On some Isaac Sim (pip) installs, `SimulationApp.is_running()` can flip false
        # shortly after startup even though stepping still works. Run for a fixed duration to
        # keep the IsaacLab sensor pipeline alive for GIL closed-loop tests.
        run_s = float(os.environ.get("GIL_ISAACLAB_RUN_S", "600") or 600.0)
        t_start = time.time()
        last_update_warn = 0.0
        last_tick = 0.0
        try:
            is_run = bool(getattr(app, "is_running", lambda: None)())
        except Exception:
            is_run = False
        print(f"[GIL] entering run loop run_s={run_s} app_is_running={is_run}", flush=True)
        while (time.time() - t_start) < run_s:
            # Pump Kit update loop (keeps GUI responsive and drives rendering).
            # Without this, some installs exit shortly after startup and camera sensors may not produce frames.
            try:
                app.update()
            except Exception as exc:
                now = time.time()
                if now - last_update_warn >= 2.0:
                    last_update_warn = now
                    print(f"[GIL] WARNING: app.update() failed; continuing ({exc!r})", flush=True)
            if kind == "humanoid":
                # Reset requested by controls (e.g. after fall).
                try:
                    if bridge.consume_reset():
                        obs, _ = play_env.reset()
                        actions.zero_()
                except Exception:
                    pass
                vx, vy, wz = bridge.vel()
                _apply_velocity_command(play_env, vx, vy, wz)
                with torch.inference_mode():
                    if policy is not None:
                        obs_in = obs["policy"] if isinstance(obs, dict) and "policy" in obs else obs
                        actions = policy(obs_in)
                    try:
                        obs, *_ = play_env.step(actions)
                    except Exception as exc:
                        import traceback

                        print(f"[GIL] ERROR: env.step failed: {exc!r}", flush=True)
                        traceback.print_exc()
                        break
            else:
                # Arm mode: treat incoming websocket commands as the control source.
                target_cmd, gripper_open_cmd = bridge.arm_command()
                with torch.inference_mode():
                    pol = obs.get("policy") if isinstance(obs, dict) else None
                    if isinstance(pol, dict) and "eef_pos" in pol:
                        eef_pos_w = pol["eef_pos"]
                        eef0 = eef_pos_w[0] if hasattr(eef_pos_w, "shape") and len(getattr(eef_pos_w, "shape")) >= 2 else eef_pos_w
                        # Convert target from command coords (x, y-up, z) into world coords (x, y, z-up).
                        tx = float(target_cmd.get("x", 0.0) or 0.0)
                        ty_up = float(target_cmd.get("y", 0.3) or 0.3)
                        tz = float(target_cmd.get("z", 0.0) or 0.0)
                        tgt_w = torch.tensor([tx, tz, ty_up], device=eef0.device, dtype=eef0.dtype)
                        err = tgt_w - eef0
                        max_step = 0.03
                        dpos = torch.clamp(err, -max_step, max_step)
                        # action layout: [dx,dy,dz, droll,dpitch,dyaw, gripper]
                        actions.zero_()
                        if actions.shape[-1] >= 3:
                            actions[:, 0:3] = dpos
                        if actions.shape[-1] >= 7:
                            actions[:, 6] = 1.0 if gripper_open_cmd else -1.0
                    else:
                        actions.zero_()
                    try:
                        obs, *_ = play_env.step(actions)
                    except Exception as exc:
                        import traceback

                        print(f"[GIL] ERROR: env.step failed: {exc!r}", flush=True)
                        traceback.print_exc()
                        break
            # Avoid a tight CPU loop on fast GPUs.
            time.sleep(0.001)
            now2 = time.time()
            if now2 - last_tick >= 5.0:
                last_tick = now2
                print(f"[GIL] tick t={now2 - t_start:.1f}s", flush=True)
            # Prefer streaming an actual Isaac Lab camera observation when available (factory sensor).
            try:
                if isinstance(obs, dict) and "policy" in obs and isinstance(obs["policy"], dict):
                    pol = obs["policy"]
                    for cam_key in ("table_cam", "wrist_cam"):
                        if cam_key in pol:
                            # Expect shape (N,H,W,3) uint8. Take env 0.
                            rgb = pol[cam_key]
                            if hasattr(rgb, "shape") and len(getattr(rgb, "shape")) == 4:
                                rgb0 = rgb[0]
                            else:
                                rgb0 = rgb
                            jpg = _encode_rgb_jpeg(rgb0, quality=int(os.environ.get("GIL_CAM_JPEG_QUALITY", "70") or "70"))
                            bridge.set_image_jpeg(jpg)
                            break
            except Exception:
                pass
            # If camera RGB isn't in observations (common for humanoid tasks), try direct sensor access.
            now = time.time()
            if now - last_cam_stream >= 0.25:
                _maybe_stream_scene_camera()
                last_cam_stream = now
            try:
                robot = env.unwrapped.scene["robot"]
                if kind == "humanoid":
                    pos = robot.data.root_pos_w[0]
                    yaw = float(robot.data.heading_w[0]) if hasattr(robot.data, "heading_w") else 0.0
                    bx, by, bz = float(pos[0]), float(pos[1]), float(pos[2])
                    bridge.set_base(bx, by, bz, yaw)
                    if live_map is not None and str(os.environ.get("GIL_STREAM_LIVE_MAP", "0") or "0").strip().lower() in ("1", "true", "yes", "on"):
                        if now - last_map >= 0.2:
                            vx, vy, wz = bridge.vel()
                            png = live_map.render(bx, by, yaw, vx, vy, wz)
                            bridge.set_extra_png("map", png)
                            last_map = now
                else:
                    # Stream arm critical state so controls can validate motion.
                    pol = obs.get("policy") if isinstance(obs, dict) else None
                    ee_cmd = {"x": 0.0, "y": 0.0, "z": 0.0}
                    if isinstance(pol, dict) and "eef_pos" in pol:
                        eef = pol["eef_pos"]
                        eef0 = eef[0] if hasattr(eef, "shape") and len(getattr(eef, "shape")) >= 2 else eef
                        wx, wy, wz_up = float(eef0[0]), float(eef0[1]), float(eef0[2])
                        # Convert world->command coords: (x, y-up, z) = (world.x, world.z, world.y)
                        ee_cmd = {"x": wx, "y": wz_up, "z": wy}
                    joints = {}
                    try:
                        jpos = robot.data.joint_pos[0]
                        names = list(getattr(robot, "joint_names", [])) or list(getattr(robot.data, "joint_names", []))
                        if names and hasattr(jpos, "__len__") and len(names) == int(jpos.shape[-1]):
                            joints = {str(n): float(jpos[i]) for i, n in enumerate(names)}
                    except Exception:
                        joints = {}
                    gr_open = bool(gripper_open_cmd)
                    bridge.set_arm_state(end_effector=ee_cmd, joints=joints, gripper_open=gr_open)
            except Exception:
                pass
    finally:
        bridge.stop()
        env.close()
        app.close()


if __name__ == "__main__":
    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    main()
