import asyncio
import json
import math
import random
import time

import websockets


class MockSim:
    """A tiny simulator stub that responds to controls commands and sends scene_state updates.

    This lets us run RL/SAC without the Three.js frontend.
    """

    def __init__(self):
        self.ee = [0.9, 0.3, 0.0]
        self.base = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
        self.mode = "external"
        self.joints = {
            "base": 90,
            "shoulder": 48,
            "elbow": 97,
            "wristPitch": 35,
            "wristRoll": 0,
            "gripper": 0,
        }

    def _emit_state(self):
        return {
            "type": "scene_state",
            "robot_kind": "humanoid",
            "joint_positions": self.joints,
            "end_effector": {"x": self.ee[0], "y": self.ee[1], "z": self.ee[2]},
            "base": self.base,
            "mode": self.mode,
            "sensors": {
                "mock": {
                    "connected": True,
                    "time_s": time.time(),
                }
            },
        }

    async def run(self, url="ws://127.0.0.1:8766"):
        # Retry connect (controls server may still be starting)
        ws = None
        for _ in range(100):
            try:
                ws = await websockets.connect(url)
                break
            except Exception as e:
                last_err = e
                await asyncio.sleep(0.1)
        if ws is None:
            print("[MOCK_SIM] Failed to connect:", repr(last_err))
            return

        async with ws:
            print("[MOCK_SIM] Connected to controls WS:", url)
            await ws.send(json.dumps(self._emit_state()))
            last_state = time.time()

            while True:
                # Send periodic state (20Hz) even if no commands
                now = time.time()
                if now - last_state >= 0.05:
                    await ws.send(json.dumps(self._emit_state()))
                    last_state = now

                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.02)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    print("[MOCK_SIM] Connection closed by server.")
                    return

                try:
                    cmd = json.loads(msg)
                except Exception:
                    continue

                t = cmd.get("type")
                if t == "move_robot":
                    self.ee = [float(cmd.get("x", self.ee[0])), float(cmd.get("y", self.ee[1])), float(cmd.get("z", self.ee[2]))]
                    await ws.send(json.dumps(self._emit_state()))
                elif t == "set_angles":
                    angles = cmd.get("angles") or {}
                    if isinstance(angles, dict):
                        self.joints.update(angles)
                    await ws.send(json.dumps(self._emit_state()))
                elif t == "reset_scene":
                    self.ee = [0.9, 0.3, 0.0]
                    self.base = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
                    await ws.send(json.dumps(self._emit_state()))
                elif t == "reset_episode":
                    self.ee = [0.9, 0.3, 0.0]
                    self.base = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
                    await ws.send(json.dumps(self._emit_state()))
                elif t == "walker_mode":
                    self.mode = str(cmd.get("mode", self.mode))
                    await ws.send(json.dumps(self._emit_state()))
                elif t == "set_goal":
                    await ws.send(json.dumps(self._emit_state()))
                elif t == "cmd_vel":
                    dt = float(cmd.get("dt", cmd.get("duration_s", 0.05)))
                    vx = float(cmd.get("vx", 0.0))
                    vy = float(cmd.get("vy", 0.0))
                    wz = float(cmd.get("wz", 0.0))
                    yaw = float(self.base.get("yaw", 0.0))
                    self.base["x"] = float(self.base.get("x", 0.0)) + (vx * math.cos(yaw) - vy * math.sin(yaw)) * dt
                    self.base["y"] = float(self.base.get("y", 0.0)) + (vx * math.sin(yaw) + vy * math.cos(yaw)) * dt
                    self.base["yaw"] = yaw + wz * dt
                    await ws.send(json.dumps(self._emit_state()))
                else:
                    # ignore gripper/unknown
                    pass


async def main():
    sim = MockSim()
    await sim.run()


if __name__ == "__main__":
    asyncio.run(main())


