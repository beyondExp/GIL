from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gil.agents.ollama_agent import OllamaDiscreteChoiceAgent
from gil.orchestrator.mcp_humanoid_adapter import McpHumanoidAdapter


def _b64_from_data_url(data_url: str) -> bytes | None:
    if not isinstance(data_url, str) or not data_url.startswith("data:image") or "," not in data_url:
        return None
    try:
        _h, b64 = data_url.split(",", 1)
        return base64.b64decode(b64)
    except Exception:
        return None


def _image_features(data_url: str) -> dict[str, Any]:
    """
    Convert a camera frame into a small *topology-free* perception summary.
    We intentionally do NOT attempt to infer maze structure, coordinates, or a map.
    """
    raw = _b64_from_data_url(data_url)
    if not raw:
        return {"ok": False, "reason": "missing_or_decode_failed"}
    try:
        from PIL import Image
        import numpy as np
    except Exception as exc:
        return {"ok": False, "reason": "pillow_or_numpy_missing", "error": repr(exc)}

    try:
        im = Image.open(io.BytesIO(raw)).convert("L")
        # Keep tiny to be cheap; this is not "vision", it's just a local signal summary.
        im = im.resize((96, 54))
        arr = np.asarray(im, dtype="float32")  # (H,W)
        h, w = int(arr.shape[0]), int(arr.shape[1])

        def _stats(a):
            return {
                "mean": float(a.mean()),
                "std": float(a.std()),
                "min": float(a.min()),
                "max": float(a.max()),
            }

        # Approx edge strength (cheap gradient magnitude proxy).
        gx = np.abs(arr[:, 1:] - arr[:, :-1]).mean()
        gy = np.abs(arr[1:, :] - arr[:-1, :]).mean()
        edge = float(gx + gy)

        # Left/center/right bands.
        x0 = w // 3
        x1 = 2 * w // 3
        left = arr[:, :x0]
        center = arr[:, x0:x1]
        right = arr[:, x1:]

        return {
            "ok": True,
            "shape": [h, w],
            "global": {**_stats(arr), "edge": edge},
            "bands": {"left": _stats(left), "center": _stats(center), "right": _stats(right)},
        }
    except Exception as exc:
        return {"ok": False, "reason": "decode_error", "error": repr(exc)}


@dataclass(frozen=True)
class _Action:
    name: str
    vx: float
    wz: float
    dt: float


def _wrap_pi(a: float) -> float:
    return float((a + math.pi) % (2.0 * math.pi) - math.pi)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Ollama tool-driven maze agent (no topology provided).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_id", default="unitree_h1_sim")
    ap.add_argument("--runtime_s", type=float, default=300.0)
    ap.add_argument("--tick_s", type=float, default=0.35)
    ap.add_argument("--reset_first", action="store_true")
    ap.add_argument("--min_z", type=float, default=0.65)
    ap.add_argument("--fall_z", type=float, default=0.55)
    ap.add_argument("--fall_persist_s", type=float, default=0.9)
    ap.add_argument("--model", default=None, help="Override OLLAMA_MODEL for this run.")
    ap.add_argument("--ollama_timeout_s", type=float, default=180.0, help="HTTP timeout for a single Ollama decision.")
    ap.add_argument(
        "--log_path",
        default="",
        help="Optional JSONL log path. If empty, logs to assets/ollama_runs/<run_id>.jsonl",
    )
    args = ap.parse_args()

    if args.model:
        import os

        os.environ["OLLAMA_MODEL"] = str(args.model)
    # Ensure the Ollama agent uses a generous timeout for slower models / first-token latency.
    try:
        import os

        os.environ["OLLAMA_TIMEOUT_S"] = str(float(args.ollama_timeout_s))
    except Exception:
        pass

    # Bounded action menu (safe, discrete). The LLM can only pick among these.
    actions: list[_Action] = [
        _Action("forward", vx=0.12, wz=0.0, dt=0.30),
        _Action("turn_left", vx=0.00, wz=0.35, dt=0.35),
        _Action("turn_right", vx=0.00, wz=-0.35, dt=0.35),
        _Action("forward_left", vx=0.10, wz=0.20, dt=0.30),
        _Action("forward_right", vx=0.10, wz=-0.20, dt=0.30),
        _Action("stop", vx=0.0, wz=0.0, dt=0.10),
    ]

    agent = OllamaDiscreteChoiceAgent()

    run_id = f"ollama_maze_{int(time.time())}"
    log_path = str(args.log_path or "").strip()
    if not log_path:
        log_path = str(Path("assets") / "ollama_runs" / f"{run_id}.jsonl")
    log_file: Path | None = None
    try:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("", encoding="utf-8")
    except Exception:
        log_file = None

    header = {"run_id": run_id, "ollama_model": agent.cfg.model, "note": "NO_TOPOLOGY", "log_path": log_path if log_file else None}
    print(json.dumps(header, indent=2), flush=True)
    if log_file:
        try:
            log_file.write_text(json.dumps(header) + "\n", encoding="utf-8")
        except Exception:
            pass

    last_xy: tuple[float, float] | None = None
    last_yaw: float | None = None
    last_progress_t = time.time()
    low_z_since: float | None = None

    async with McpHumanoidAdapter(url=str(args.url), robot_id=str(args.robot_id)) as ad:
        # Safety setup. IMPORTANT: do NOT ingest a maze seed/spec/topology here.
        await ad.stop(reason="ollama_maze_setup")
        await ad.disable_motion(reason="ollama_maze_setup")
        if bool(args.reset_first) and ad.capabilities().supports_reset_episode:
            await ad.reset_episode()
            await asyncio.sleep(0.8)
        await ad.set_mode("external")
        await ad.heartbeat(source="ollama_maze_setup")
        en = await ad.enable_motion(reason="ollama_maze")
        if str(en.get("status") or "").lower() == "error":
            print(json.dumps({"ok": False, "error": "enable_failed", "enable": en}, indent=2), flush=True)
            return 2

        t0 = time.time()
        step = 0
        while (time.time() - t0) < float(args.runtime_s):
            step += 1

            # Observe: camera + proprioception only.
            obs = await ad.call_tool("get_observation", {"robot_kind": "humanoid"})
            st = (obs.get("state") or {}) if isinstance(obs, dict) else {}
            base = (st.get("base") or {}) if isinstance(st, dict) else {}
            x = float(base.get("x", 0.0) or 0.0)
            y = float(base.get("y", 0.0) or 0.0)
            z = float(base.get("z", 0.0) or 0.0)
            yaw = float(base.get("yaw", 0.0) or 0.0)
            img = str(obs.get("image") or "")

            # Fall detection (topology-free safety).
            if 0.0 < z < float(args.fall_z):
                if low_z_since is None:
                    low_z_since = time.time()
                if (time.time() - low_z_since) >= float(args.fall_persist_s):
                    await ad.stop(reason="ollama_maze_fall")
                    await ad.disable_motion(reason="ollama_maze_fall")
                    if ad.capabilities().supports_reset_episode:
                        await ad.reset_episode()
                        await asyncio.sleep(0.8)
                        await ad.set_mode("external")
                        await ad.enable_motion(reason="ollama_maze_post_fall")
                    low_z_since = None
                    last_xy = None
                    last_yaw = None
                    last_progress_t = time.time()
                    continue
            else:
                low_z_since = None

            feat = _image_features(img) if img else {"ok": False, "reason": "missing"}

            # Progress estimate (no map): just did we move at all?
            moved_m = 0.0
            dyaw = 0.0
            if last_xy is not None:
                moved_m = float(math.hypot(x - last_xy[0], y - last_xy[1]))
            if last_yaw is not None:
                dyaw = float(abs(_wrap_pi(yaw - last_yaw)))
            # Treat small-but-real motion as progress; we only want "stalled" when basically stuck.
            if moved_m >= 0.012 or dyaw >= 0.10:
                last_progress_t = time.time()
            stalled_s = float(time.time() - last_progress_t)

            # Discrete tool menu for Ollama.
            # If we're stalled, *constrain the menu* so the LLM can't keep selecting "forward" forever.
            offer_actions: list[_Action]
            if stalled_s > 3.0:
                offer_actions = [a for a in actions if a.name in ("turn_left", "turn_right", "stop")]
            else:
                offer_actions = list(actions)

            options = []
            for i, a in enumerate(offer_actions):
                options.append(
                    {
                        "i": i,
                        "name": a.name,
                        "tool": "drive_humanoid",
                        "args": {"vx": a.vx, "vy": 0.0, "wz": a.wz, "duration_s": a.dt},
                        "constraints": {"vx_range": [-0.12, 0.12], "wz_range": [-0.35, 0.35], "dt_range": [0.1, 0.35]},
                    }
                )

            ctx = {
                # No maze topology, no seed/spec, no map.
                "task": "Escape the maze. You do NOT have a map. Explore safely using only local perception.",
                "perception": feat,
                "proprioception": {"x": x, "y": y, "z": z, "yaw": yaw},
                "last_step": {"moved_m": moved_m, "abs_dyaw": dyaw, "stalled_s": stalled_s},
                "safety": {"min_z": float(args.min_z), "fall_z": float(args.fall_z)},
                "hard_rules": [
                    "Return strict JSON with a valid integer 'choice'.",
                    "If stalled_s > 3.0, you MUST choose a turn action (turn_left or turn_right).",
                    "Avoid repeating the same turn direction forever; if still stalled after turning, try the other direction.",
                    "Only choose stop if you think you are falling / unsafe.",
                ],
                "rule": "Prefer forward when moving freely; when blocked/stalled, turn to search for an opening, then continue.",
            }

            # Ask Ollama to choose among bounded actions. If Ollama stalls/errors, fail-safe to STOP and retry next tick.
            sel: dict[str, Any] = {"choice": max(0, len(offer_actions) - 1), "reason": "ollama_error_fallback"}
            try:
                sel = agent.choose(task="maze_escape_no_topology", options=options, context=ctx)
            except Exception as exc:
                sel = {"choice": max(0, len(offer_actions) - 1), "reason": f"ollama_exception:{type(exc).__name__}"}

            choice = int(sel.get("choice", max(0, len(offer_actions) - 1)))
            choice = max(0, min(len(offer_actions) - 1, choice))
            a = offer_actions[choice]

            # Execute the chosen tool (with auto-recover if motion got disabled).
            if a.name == "stop":
                res = await ad.stop(reason="ollama_choice_stop")
            else:
                res = await ad.drive_cmd_vel(vx=float(a.vx), vy=0.0, wz=float(a.wz), duration_s=float(a.dt), reason=f"ollama:{a.name}")
                if isinstance(res, dict) and str(res.get("error") or "").lower().startswith("motion is disabled"):
                    await ad.enable_motion(reason="ollama_reenable")
                    res = await ad.drive_cmd_vel(
                        vx=float(a.vx), vy=0.0, wz=float(a.wz), duration_s=float(a.dt), reason=f"ollama:{a.name}:retry"
                    )

            print(
                json.dumps(
                    {
                        "t": time.time(),
                        "step": step,
                        "choice": {"i": choice, "name": a.name, "reason": str(sel.get("reason") or "")[:160]},
                        "base": {"x": x, "y": y, "z": z, "yaw": yaw},
                        "progress": {"moved_m": moved_m, "abs_dyaw": dyaw, "stalled_s": stalled_s},
                        "perception_ok": bool(feat.get("ok")),
                        "tool_result": res,
                    }
                ),
                flush=True,
            )
            if log_file:
                try:
                    line = json.dumps(
                        {
                            "t": time.time(),
                            "step": step,
                            "choice": {"i": choice, "name": a.name, "reason": str(sel.get("reason") or "")[:160]},
                            "base": {"x": x, "y": y, "z": z, "yaw": yaw},
                            "progress": {"moved_m": moved_m, "abs_dyaw": dyaw, "stalled_s": stalled_s},
                            "perception_ok": bool(feat.get("ok")),
                            "tool_result": res,
                        }
                    )
                    with log_file.open("a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass

            last_xy = (x, y)
            last_yaw = yaw
            await asyncio.sleep(float(args.tick_s))

        await ad.stop(reason="ollama_maze_done")
        await ad.disable_motion(reason="ollama_maze_done")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

