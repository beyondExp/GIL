import argparse
import asyncio
import json
import os
from typing import Optional

import numpy as np

from diffusion_policy import CondEncoder, DenoiseMLP, DiffusionConfig, GaussianDiffusion
from offline_dataset import make_state_vector


def torch_required():
    try:
        import torch  # noqa: F401
    except Exception as e:
        raise RuntimeError("PyTorch is required. Install deps from requirements-ml.txt") from e


def load_image_tensor_from_data_url(data_url: str, size: int):
    import base64
    from io import BytesIO
    from PIL import Image
    import torch

    s = data_url
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    raw = base64.b64decode(s)
    img = Image.open(BytesIO(raw)).convert("RGB").resize((size, size))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    x = torch.from_numpy(arr).permute(2, 0, 1)
    return x


def load_model(ckpt_path: str, device):
    torch_required()
    import torch

    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    cfg = DiffusionConfig(**ckpt["cfg"])
    cond = CondEncoder(cfg).to(device)
    denoise = DenoiseMLP(cfg).to(device)
    diff = GaussianDiffusion(cfg).to(device)

    cond.img_enc.net.load_state_dict(ckpt["cond_enc"]["img"])
    cond.state_enc.load_state_dict(ckpt["cond_enc"]["state"])
    cond.fuse.load_state_dict(ckpt["cond_enc"]["fuse"])
    denoise.net.load_state_dict(ckpt["denoise"])
    cond.eval()
    denoise.eval()
    return cfg, cond, denoise, diff


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True, help="Diffusion policy checkpoint (diffusion_pick_place.pt)")
    ap.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--horizon", type=int, default=250)
    ap.add_argument("--max-delta", type=float, default=0.18)
    ap.add_argument("--settle-updates", type=int, default=2)
    ap.add_argument("--cube-name", type=str, default="cube_blue")
    ap.add_argument("--seed-start", type=int, default=0)
    ap.add_argument("--tol-reach", type=float, default=0.15)
    ap.add_argument("--tol-bin", type=float, default=0.30)
    ap.add_argument("--chunk-len", type=int, default=16, help="How many actions to sample per chunk (should match training)")
    args = ap.parse_args()

    torch_required()
    import torch
    import websockets

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cfg, cond_enc, denoise, diff = load_model(os.path.abspath(args.checkpoint), device)

    # Override chunk length if user provides (safety)
    chunk_len = int(args.chunk_len)
    if chunk_len != int(cfg.chunk_len):
        raise ValueError(f"chunk-len mismatch: ckpt={cfg.chunk_len} arg={chunk_len}")

    # Minimal websocket client to the frontend (same port as controls server uses)
    uri = "ws://127.0.0.1:8766"
    async with websockets.connect(uri) as ws:
        last_state = {}

        async def recv_loop():
            nonlocal last_state
            async for msg in ws:
                try:
                    data = json.loads(msg)
                except Exception:
                    continue
                if data.get("type") in ("scene_state", "scene_image"):
                    last_state = data

        recv_task = asyncio.create_task(recv_loop())

        async def send(cmd: dict):
            await ws.send(json.dumps(cmd))

        async def wait_state(timeout_s=1.5):
            for _ in range(int(timeout_s / 0.05)):
                if last_state:
                    return True
                await asyncio.sleep(0.05)
            return False

        # Request an initial state
        await send({"type": "capture_image"})
        await wait_state(2.0)

        successes = 0
        for ep in range(int(args.episodes)):
            seed = int(args.seed_start + ep * 9973)
            await send({"type": "reset_scene", "seed": seed})
            await asyncio.sleep(0.2)
            await send({"type": "capture_image"})
            await asyncio.sleep(0.2)

            stage = 0
            chunk = None
            chunk_i = 0

            for t in range(int(args.horizon)):
                # ensure we have a fresh image/state occasionally
                await send({"type": "capture_image"})
                await asyncio.sleep(0.05)

                ee = last_state.get("end_effector") or {}
                objs = last_state.get("objects") or {}
                cubes = objs.get("cubes") or []
                cube = {}
                for c in cubes:
                    if isinstance(c, dict) and c.get("name") == str(args.cube_name):
                        cube = c
                        break
                bin_d = objs.get("bin") or {}

                state_vec = make_state_vector(
                    {
                        "ee_xyz": [ee.get("x", 0.9), ee.get("y", 0.3), ee.get("z", 0.0)],
                        "cube_xyz": [cube.get("x", 1.0), cube.get("y", 0.075), cube.get("z", 0.2)],
                        "bin_xyz": [bin_d.get("x", 2.0), bin_d.get("y", 0.15), bin_d.get("z", 0.0)],
                        "gripper_open": 1.0 if bool(last_state.get("gripper_open", True)) else 0.0,
                        "held": 1.0 if bool(cube.get("held", False)) else 0.0,
                    }
                )

                img_left = last_state.get("image_left") or last_state.get("image") or ""
                if not img_left:
                    # No image yet; skip
                    await asyncio.sleep(0.05)
                    continue

                if chunk is None or chunk_i >= chunk_len:
                    with torch.no_grad():
                        img_t = load_image_tensor_from_data_url(img_left, size=int(cfg.img_size)).unsqueeze(0).to(device)
                        st_t = torch.tensor([state_vec], dtype=torch.float32, device=device)
                        cond = cond_enc(img_t, st_t)
                        chunk = diff.sample(denoise, cond, device=device, batch_size=1).squeeze(0).cpu().numpy()
                        chunk_i = 0

                delta_xyz = np.clip(chunk[chunk_i], -1.0, 1.0)
                chunk_i += 1

                # Heuristic gripper
                cube_xyz = np.array(state_vec[3:6], dtype=np.float32)
                ee_xyz = np.array(state_vec[0:3], dtype=np.float32)
                bin_xyz = np.array(state_vec[6:9], dtype=np.float32)
                held = float(state_vec[-1])
                dist_ee_cube = float(np.linalg.norm(cube_xyz - ee_xyz))
                dist_cube_bin = float(np.linalg.norm(cube_xyz - bin_xyz))
                if stage == 0 and dist_ee_cube <= float(args.tol_reach):
                    stage = 1
                if stage == 1 and held >= 0.5:
                    stage = 2
                if stage == 2 and dist_cube_bin <= float(args.tol_bin):
                    stage = 3

                gr_cmd = None
                if stage <= 1 and held < 0.5:
                    gr_cmd = False
                elif stage >= 2 and held >= 0.5 and dist_cube_bin <= float(args.tol_bin):
                    gr_cmd = True

                # Execute
                ee_now = np.array([ee.get("x", 0.9), ee.get("y", 0.3), ee.get("z", 0.0)], dtype=np.float32)
                target = ee_now + (delta_xyz.astype(np.float32) * float(args.max_delta))
                target[1] = max(float(target[1]), 0.05)
                await send({"type": "move_robot", "x": float(target[0]), "y": float(target[1]), "z": float(target[2])})
                for _ in range(int(args.settle_updates)):
                    await asyncio.sleep(0.05)

                if gr_cmd is not None:
                    await send({"type": "gripper", "open": bool(gr_cmd)})
                    for _ in range(max(1, int(args.settle_updates))):
                        await asyncio.sleep(0.05)

                # Success check
                objs2 = (last_state.get("objects") or {})
                cubes2 = objs2.get("cubes") or []
                cube2 = {}
                for c in cubes2:
                    if isinstance(c, dict) and c.get("name") == str(args.cube_name):
                        cube2 = c
                        break
                bin2 = objs2.get("bin") or {}
                cube2_xyz = np.array([cube2.get("x", 0.0), cube2.get("y", 0.0), cube2.get("z", 0.0)], dtype=np.float32)
                bin2_xyz = np.array([bin2.get("x", 0.0), bin2.get("y", 0.0), bin2.get("z", 0.0)], dtype=np.float32)
                held2 = 1.0 if bool(cube2.get("held", False)) else 0.0
                if float(np.linalg.norm(cube2_xyz - bin2_xyz)) <= float(args.tol_bin) and held2 < 0.5 and stage >= 3:
                    successes += 1
                    break

            if (ep + 1) % 10 == 0:
                print(f"[DIFF-RUN] ep={ep+1}/{args.episodes} successes={successes}")

        recv_task.cancel()
        try:
            await recv_task
        except Exception:
            pass
        print(json.dumps({"status": "success", "episodes": int(args.episodes), "successes": int(successes), "success_rate": float(successes) / float(args.episodes)}))


if __name__ == "__main__":
    asyncio.run(main())


