import argparse
import asyncio
import base64
import io
import json
import math
import time
from dataclasses import dataclass

import numpy as np
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

try:
    from PIL import Image
except Exception as e:  # pragma: no cover
    Image = None  # type: ignore
    _PIL_IMPORT_ERR = e


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _decode_data_url(data_url: str) -> bytes:
    if not data_url:
        return b""
    if "," in data_url:
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    return base64.b64decode(b64)


def _pick_image_payload(img_payload: dict) -> str:
    """
    Prefer the humanoid forward camera if present in images map, else fall back to the generic 'image'.
    """
    images = (img_payload.get("images") or {}) if isinstance(img_payload, dict) else {}
    if isinstance(images, dict):
        for k in (
            "/World/Humanoid/camera_link/PerspectiveCamera_robot",
            "/World/Humanoid/camera_link/PerspectiveCamera",
        ):
            v = images.get(k)
            if isinstance(v, str) and v.startswith("data:image"):
                return v
    for k in ("image", "image_wide", "last_image", "last_image_wide"):
        v = img_payload.get(k)
        if isinstance(v, str) and v.startswith("data:image"):
            return v
    return ""


def _img_to_gray_u8(data_url: str, max_w: int = 320) -> np.ndarray:
    if Image is None:  # pragma: no cover
        raise RuntimeError(f"PIL not available: {_PIL_IMPORT_ERR!r}")
    raw = _decode_data_url(data_url)
    if not raw:
        return np.zeros((1, 1), dtype=np.uint8)
    img = Image.open(io.BytesIO(raw))
    img = img.convert("L")
    w, h = img.size
    if w > max_w:
        scale = max_w / float(w)
        img = img.resize((max_w, max(1, int(h * scale))), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def _simple_free_space_scores(gray: np.ndarray) -> tuple[float, float, float]:
    """
    Returns (left_score, center_score, right_score) in [0,1].

    Heuristic: in the lower half of the image, compute mean brightness for left/center/right ROIs.
    Brighter is assumed to be "more open" (tunable in practice).
    """
    h, w = gray.shape[:2]
    y0 = int(h * 0.55)
    y1 = int(h * 0.95)
    x0 = int(w * 0.08)
    x1 = int(w * 0.92)
    roi = gray[y0:y1, x0:x1]
    if roi.size < 10:
        return 0.5, 0.5, 0.5

    rw = roi.shape[1]
    l = roi[:, : max(1, rw // 3)]
    c = roi[:, max(1, rw // 3) : max(2, 2 * rw // 3)]
    r = roi[:, max(2, 2 * rw // 3) :]

    # Normalize brightness into [0,1]
    ls = float(np.mean(l)) / 255.0
    cs = float(np.mean(c)) / 255.0
    rs = float(np.mean(r)) / 255.0
    return ls, cs, rs


@dataclass
class StepDecision:
    vx: float
    wz: float
    reason: str


def _decide(ls: float, cs: float, rs: float, *, vx_nom: float, wz_max: float) -> StepDecision:
    """
    Very simple corridor-following:
    - steer towards the "brighter" side
    - slow down (and turn harder) if center looks blocked
    """
    # Steering: positive wz turns left
    steer = float(np.clip((ls - rs) * 2.2, -1.0, 1.0))

    # If the center is "dark", treat as close obstacle and bias turning.
    # (Threshold is heuristic; tune as needed for your maze visuals.)
    if cs < 0.28:
        vx = vx_nom * 0.15
        wz = float(np.clip(steer * 1.6, -wz_max, wz_max))
        return StepDecision(vx=vx, wz=wz, reason=f"avoid_obstacle cs={cs:.2f} steer={steer:.2f}")

    vx = vx_nom
    wz = float(np.clip(steer * wz_max, -wz_max, wz_max))
    return StepDecision(vx=vx, wz=wz, reason=f"follow_corridor cs={cs:.2f} steer={steer:.2f}")


async def _call_json(session: ClientSession, tool: str, params: dict) -> dict:
    res = await session.call_tool(tool, params)
    txt = res.content[0].text if res.content else ""
    return json.loads(txt) if txt else {}


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    p.add_argument("--runtime_s", type=float, default=90.0)
    p.add_argument("--step_s", type=float, default=0.25)
    p.add_argument("--vx", type=float, default=0.35)
    p.add_argument("--wz_max", type=float, default=1.0)
    p.add_argument("--print_every", type=int, default=8)
    args = p.parse_args()

    print(f"[vision] Connecting to {args.url}", flush=True)
    async with streamablehttp_client(args.url, timeout=30, sse_read_timeout=30) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("set_humanoid_mode", {"mode": "external"})

            t0 = time.time()
            step = 0
            last_print = 0

            while time.time() - t0 < float(args.runtime_s):
                step += 1

                st = await _call_json(session, "get_robot_state_for", {"robot_kind": "humanoid"})
                base = st.get("base") or {}
                x = float(base.get("x", 0.0) or 0.0)
                y = float(base.get("y", 0.0) or 0.0)
                yaw = float(base.get("yaw", 0.0) or 0.0)

                img_payload = await _call_json(session, "get_latest_image_for", {"robot_kind": "humanoid"})
                data_url = _pick_image_payload(img_payload)
                if not data_url:
                    # No image: drive slowly forward and keep slight turn to avoid deadlock.
                    await session.call_tool(
                        "drive_humanoid",
                        {"vx": 0.15, "vy": 0.0, "wz": 0.3, "duration_s": float(args.step_s), "reason": "no_image_fallback"},
                    )
                    continue

                gray = _img_to_gray_u8(data_url, max_w=320)
                ls, cs, rs = _simple_free_space_scores(gray)
                dec = _decide(ls, cs, rs, vx_nom=float(args.vx), wz_max=float(args.wz_max))

                await session.call_tool(
                    "drive_humanoid",
                    {"vx": float(dec.vx), "vy": 0.0, "wz": float(dec.wz), "duration_s": float(args.step_s), "reason": dec.reason},
                )

                if step - last_print >= int(args.print_every):
                    last_print = step
                    print(
                        f"[vision] step={step:04d} pos=({x:.2f},{y:.2f}) yaw={yaw:.2f} "
                        f"scores L/C/R=({ls:.2f},{cs:.2f},{rs:.2f}) cmd(vx={dec.vx:.2f},wz={dec.wz:.2f})",
                        flush=True,
                    )


if __name__ == "__main__":
    asyncio.run(main())


