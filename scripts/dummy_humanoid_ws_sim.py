from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import websockets


def _now_ms() -> int:
    return int(time.time() * 1000)


def _monotonic_s() -> float:
    return float(time.monotonic())


def _make_noise_image_data_url(w: int = 320, h: int = 180) -> str:
    """Generate a simple labeled JPEG data URL that passes image_gate.

    This is intentionally *not* a real camera feed. It exists so the UI can prove
    end-to-end streaming works even without Isaac Sim.
    """
    try:
        from PIL import Image
        from PIL import ImageDraw, ImageFont
        import numpy as np
    except Exception:
        return ""
    rng = np.random.default_rng(2)
    base = np.linspace(25, 210, w, dtype=np.float32)[None, :].repeat(h, axis=0)
    noise = rng.normal(0, 10, size=(h, w)).astype(np.float32)
    img = np.clip(base + noise, 0, 255).astype("uint8")
    im = Image.fromarray(img, mode="L").convert("RGB")
    d = ImageDraw.Draw(im)
    # Crosshair
    d.line([(w // 2, 0), (w // 2, h)], fill=(255, 255, 255), width=1)
    d.line([(0, h // 2), (w, h // 2)], fill=(255, 255, 255), width=1)
    d.rectangle([6, 6, w - 6, h - 6], outline=(0, 0, 0), width=2)
    # Label
    txt = "DUMMY FPV (sim_ws)"
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    d.rectangle([8, 8, 8 + 140, 26], fill=(0, 0, 0))
    d.text((12, 11), txt, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    z: float = 1.05
    yaw: float = 0.0


class DummyHumanoidSim:
    def __init__(self) -> None:
        self.pose = Pose()
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self.last_cmd_at = 0.0
        self.image = _make_noise_image_data_url()
        self._last_img_ms = 0
        self.mode = "external"
        self._goal: tuple[float, float] | None = None
        self.camera_info = {
            "width": 320,
            "height": 180,
            "fx": 240.0,
            "fy": 240.0,
            "cx": 160.0,
            "cy": 90.0,
            "model": "pinhole",
            "dist": [0.0, 0.0, 0.0, 0.0, 0.0],
        }

    def _step(self, dt: float) -> None:
        # If goal mode is set, steer toward goal with a naive unicycle.
        if self.mode == "goal" and self._goal is not None:
            gx, gy = self._goal
            heading = math.atan2(gy - self.pose.y, gx - self.pose.x)
            err = (heading - self.pose.yaw + math.pi) % (2 * math.pi) - math.pi
            self.wz = max(-0.8, min(0.8, 1.8 * err))
            self.vx = 0.45 if abs(err) < 0.8 else 0.10
            self.vy = 0.0

        # Integrate cmd_vel in robot frame into world.
        c = math.cos(self.pose.yaw)
        s = math.sin(self.pose.yaw)
        self.pose.x += (self.vx * c - self.vy * s) * dt
        self.pose.y += (self.vx * s + self.vy * c) * dt
        self.pose.yaw = (self.pose.yaw + self.wz * dt + math.pi) % (2 * math.pi) - math.pi
        # Refresh placeholder FPV at low rate (cheap, keeps UI "alive").
        now = _now_ms()
        if now - self._last_img_ms > 750:
            self.image = _make_noise_image_data_url()
            self._last_img_ms = now

        # Decay commands if no keepalive.
        if time.time() - self.last_cmd_at > 0.35:
            self.vx *= 0.5
            self.vy *= 0.5
            self.wz *= 0.5
            if abs(self.vx) < 1e-3:
                self.vx = 0.0
            if abs(self.vy) < 1e-3:
                self.vy = 0.0
            if abs(self.wz) < 1e-3:
                self.wz = 0.0

    def apply_command(self, cmd: dict[str, Any]) -> None:
        t = str(cmd.get("type") or "")
        if t in ("cmd_vel", "preview_vel"):
            self.vx = float(cmd.get("vx", 0.0) or 0.0)
            self.vy = float(cmd.get("vy", 0.0) or 0.0)
            self.wz = float(cmd.get("wz", 0.0) or 0.0)
            self.last_cmd_at = time.time()
            return
        if t == "stop":
            self.vx = self.vy = self.wz = 0.0
            self.last_cmd_at = time.time()
            return
        if t == "reset_episode":
            self.pose.x = float(cmd.get("x", 0.0) or 0.0)
            self.pose.y = float(cmd.get("y", 0.0) or 0.0)
            self.pose.z = float(cmd.get("z", 1.05) or 1.05)
            self.pose.yaw = float(cmd.get("yaw", 0.0) or 0.0)
            self.vx = self.vy = self.wz = 0.0
            self.last_cmd_at = time.time()
            return
        if t == "walker_mode":
            m = str((cmd.get("mode") or cmd.get("payload", {}).get("mode") or "external")).lower().strip()
            self.mode = "goal" if m == "goal" else "external"
            return
        if t == "set_goal":
            self._goal = (float(cmd.get("x", 0.0) or 0.0), float(cmd.get("y", 0.0) or 0.0))
            return

    def state_message(self) -> dict[str, Any]:
        return {
            "type": "scene_state",
            "robot_kind": "humanoid",
            "_observed_at_ms": _now_ms(),
            "_observed_at_monotonic_s": _monotonic_s(),
            "mode": self.mode,
            "base": {"x": self.pose.x, "y": self.pose.y, "z": self.pose.z, "yaw": self.pose.yaw},
            "sensors": {"imu": True, "contacts": True},
            "last_image": self.image,
            "camera_info": self.camera_info,
        }


async def main() -> None:
    host = str(os.getenv("GIL_WS_BIND", "127.0.0.1") or "127.0.0.1")
    port = int(os.getenv("GIL_WS_PORT", "8766") or 8766)
    url = f"ws://{host}:{port}"
    hz = float(os.getenv("DUMMY_SIM_HZ", "10") or 10)
    time_scale = float(os.getenv("DUMMY_SIM_TIME_SCALE", "1.0") or 1.0)
    dt = 1.0 / max(1.0, hz)

    sim = DummyHumanoidSim()

    while True:
        try:
            async with websockets.connect(url, ping_interval=10, ping_timeout=10) as ws:
                # Hello handshake.
                await ws.send(json.dumps({"type": "hello", "robot_kind": "humanoid"}))

                async def tx_loop():
                    while True:
                        sim._step(dt * max(0.01, time_scale))
                        await ws.send(json.dumps(sim.state_message()))
                        await asyncio.sleep(dt)

                async def rx_loop():
                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                        except Exception:
                            continue
                        if isinstance(data, dict):
                            sim.apply_command(data)

                await asyncio.gather(tx_loop(), rx_loop())
        except Exception:
            await asyncio.sleep(0.5 + random.random() * 0.8)


if __name__ == "__main__":
    asyncio.run(main())

