import argparse
import os
from dataclasses import asdict
from typing import List, Tuple

import numpy as np

from diffusion_policy import CondEncoder, DenoiseMLP, DiffusionConfig, GaussianDiffusion
from offline_dataset import load_transitions, make_state_vector, split_by_episode


def _torch_required():
    try:
        import torch  # noqa: F401
    except Exception as e:
        raise RuntimeError("PyTorch is required. Install deps from requirements-ml.txt") from e


def load_image_tensor(path: str, size: int):
    from PIL import Image
    import torch

    img = Image.open(path).convert("RGB").resize((size, size))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    x = torch.from_numpy(arr).permute(2, 0, 1)  # CHW
    return x


def build_windows(run_dir: str, chunk_len: int) -> List[Tuple[str, List[float], np.ndarray]]:
    """Return list of (image_path, state_vec, action_chunk[L,3]).

    Important: We only require an image at the *start* of the chunk.
    Actions are taken from consecutive timesteps even if intermediate steps were not image-recorded.
    This makes training compatible with datasets collected with record_every > 1.
    """
    trs = load_transitions(run_dir)
    by_ep = split_by_episode(trs)
    windows: List[Tuple[str, List[float], np.ndarray]] = []
    for _ep, seq in by_ep.items():
        if len(seq) < chunk_len:
            continue
        for i in range(0, len(seq) - chunk_len + 1):
            base = seq[i]
            if not base.image_left_path or not os.path.exists(base.image_left_path):
                continue
            # require contiguous time indices across the chunk (per-step actions)
            ok = True
            for k in range(1, chunk_len):
                if seq[i + k].t != base.t + k:
                    ok = False
                    break
            if not ok:
                continue
            acts = np.stack([np.array(seq[i + k].action_delta_xyz, dtype=np.float32) for k in range(chunk_len)], axis=0)
            windows.append((base.image_left_path, make_state_vector(base.state), acts))
    return windows


def main():
    _torch_required()
    import torch
    import torch.nn.functional as F

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Dataset run dir produced by collect_pick_place_dataset")
    ap.add_argument("--chunk-len", type=int, default=16)
    ap.add_argument("--img-size", type=int, default=128)
    ap.add_argument("--diffusion-steps", type=int, default=50)
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--save-path", default="", help="Save checkpoint path (default: <run_dir>/diffusion_pick_place.pt)")
    ap.add_argument("--save-every-epochs", type=int, default=0, help="If >0, save intermediate checkpoints every N epochs")
    ap.add_argument("--max-windows", type=int, default=0, help="If >0, cap dataset windows for quick tests")
    args = ap.parse_args()

    chunk_len = int(max(4, min(64, args.chunk_len)))
    img_size = int(max(64, min(256, args.img_size)))
    diffusion_steps = int(max(10, min(200, args.diffusion_steps)))

    windows = build_windows(args.run_dir, chunk_len=chunk_len)
    print(f"[DIFF] built_windows={len(windows)} (pre-cap) run_dir={os.path.abspath(args.run_dir)}")
    if args.max_windows and int(args.max_windows) > 0:
        windows = windows[: int(args.max_windows)]
        print(f"[DIFF] capped_windows={len(windows)} (max_windows={int(args.max_windows)})")
    if not windows:
        raise RuntimeError("No training windows found. Ensure images were recorded every step and exist on disk.")

    state_dim = len(windows[0][1])
    cfg = DiffusionConfig(
        action_dim=3,
        chunk_len=chunk_len,
        img_size=img_size,
        state_dim=state_dim,
        diffusion_steps=diffusion_steps,
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cond_enc = CondEncoder(cfg).to(device)
    denoise = DenoiseMLP(cfg).to(device)
    diff = GaussianDiffusion(cfg).to(device)

    params = list(cond_enc.parameters()) + list(denoise.parameters())
    opt = torch.optim.Adam(params, lr=float(args.lr))

    save_every = int(args.save_every_epochs)
    if save_every < 0:
        save_every = 0

    idxs = np.arange(len(windows))
    for ep in range(int(args.epochs)):
        np.random.shuffle(idxs)
        losses = []
        cond_enc.train()
        denoise.train()

        for bi, i in enumerate(range(0, len(idxs), int(args.batch_size))):
            batch = [windows[j] for j in idxs[i : i + int(args.batch_size)]]
            imgs = torch.stack([load_image_tensor(p, size=img_size) for (p, _s, _a) in batch], dim=0).to(device)
            states = torch.tensor([s for (_p, s, _a) in batch], dtype=torch.float32, device=device)
            # Avoid slow list-of-ndarrays -> tensor path
            a_np = np.stack([a for (_p, _s, a) in batch], axis=0).astype(np.float32, copy=False)
            x0 = torch.from_numpy(a_np).to(device)  # (B,L,3)

            bsz = x0.shape[0]
            t = torch.randint(0, diff.T, (bsz,), device=device, dtype=torch.long)
            noise = torch.randn_like(x0)
            x_t = diff.q_sample(x0, t, noise)
            cond = cond_enc(imgs, states)
            eps_pred = denoise(x_t, t, cond)

            loss = F.mse_loss(eps_pred, noise)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            losses.append(float(loss.detach().cpu().item()))

            if (bi % 100) == 0 and bi > 0:
                print(f"[DIFF] epoch={ep+1}/{args.epochs} batch={bi} loss_avg={np.mean(losses):.6f}")

        print(f"[DIFF] epoch={ep+1}/{args.epochs} loss={np.mean(losses):.6f} windows={len(windows)}")

        # Periodic checkpointing
        if save_every > 0 and ((ep + 1) % save_every == 0):
            base_save = args.save_path.strip()
            if not base_save:
                base_save = os.path.join(os.path.abspath(args.run_dir), "diffusion_pick_place.pt")
            root, ext = os.path.splitext(base_save)
            mid_path = f"{root}_e{ep+1:03d}{ext or '.pt'}"
            ckpt = {
                "cfg": asdict(cfg),
                "cond_enc": {
                    "img": cond_enc.img_enc.net.state_dict(),
                    "state": cond_enc.state_enc.state_dict(),
                    "fuse": cond_enc.fuse.state_dict(),
                },
                "denoise": denoise.net.state_dict(),
            }
            torch.save(ckpt, mid_path)
            print(f"[DIFF] saved {os.path.abspath(mid_path)}")

    save_path = args.save_path.strip()
    if not save_path:
        save_path = os.path.join(os.path.abspath(args.run_dir), "diffusion_pick_place.pt")
    ckpt = {
        "cfg": asdict(cfg),
        "cond_enc": {
            "img": cond_enc.img_enc.net.state_dict(),
            "state": cond_enc.state_enc.state_dict(),
            "fuse": cond_enc.fuse.state_dict(),
        },
        "denoise": denoise.net.state_dict(),
    }
    torch.save(ckpt, save_path)
    print(f"[DIFF] saved {os.path.abspath(save_path)}")


if __name__ == "__main__":
    main()


