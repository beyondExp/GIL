import argparse
import os
from typing import Optional, Tuple

import numpy as np

from offline_dataset import load_transitions, make_state_vector


def torch_required():
    try:
        import torch  # noqa: F401
    except Exception as e:
        raise RuntimeError("PyTorch is required. Install deps from requirements-ml.txt") from e


def load_image_tensor(path: str, size: int = 128):
    from PIL import Image
    import torch

    img = Image.open(path).convert("RGB").resize((size, size))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    # HWC -> CHW
    x = torch.from_numpy(arr).permute(2, 0, 1)
    return x


class BCNet:
    def __init__(self, state_dim: int, img_size: int = 128, out_dim: int = 4, hidden: int = 256):
        import torch
        import torch.nn as nn

        self.img_size = img_size
        self.net = nn.Sequential(
            nn.Conv2d(3, 32, 5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(32, 64, 5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(64, 128, 5, stride=2, padding=2),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
            nn.Flatten(),
        )
        img_feat_dim = 128 * 4 * 4
        self.head = nn.Sequential(
            nn.Linear(img_feat_dim + state_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, out_dim),
        )

    def parameters(self):
        return list(self.net.parameters()) + list(self.head.parameters())

    def to(self, device):
        self.net.to(device)
        self.head.to(device)
        return self

    def train(self):
        self.net.train()
        self.head.train()

    def eval(self):
        self.net.eval()
        self.head.eval()

    def __call__(self, img, state):
        import torch

        f = self.net(img)
        x = torch.cat([f, state], dim=-1)
        return self.head(x)


def main():
    torch_required()
    import torch
    import torch.nn.functional as F

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Dataset run dir produced by collect_pick_place_dataset")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--img-size", type=int, default=128)
    ap.add_argument("--save-path", default="", help="Where to save the trained model .pt")
    args = ap.parse_args()

    trs = load_transitions(args.run_dir)
    # Filter transitions that have an image path written
    trs = [t for t in trs if t.image_left_path and os.path.exists(t.image_left_path)]
    if not trs:
        raise RuntimeError("No usable transitions with images found. Did recording fail to write frames?")

    state_dim = len(make_state_vector(trs[0].state))
    model = BCNet(state_dim=state_dim, img_size=args.img_size).to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
    opt = torch.optim.Adam(model.parameters(), lr=float(args.lr))

    # Build tensors on the fly (simple baseline)
    idxs = np.arange(len(trs))
    for ep in range(int(args.epochs)):
        np.random.shuffle(idxs)
        losses = []
        model.train()
        for i in range(0, len(idxs), int(args.batch_size)):
            batch = [trs[j] for j in idxs[i : i + int(args.batch_size)]]
            imgs = torch.stack([load_image_tensor(t.image_left_path, size=args.img_size) for t in batch], dim=0).to(model.net[0].weight.device)
            states = torch.tensor([make_state_vector(t.state) for t in batch], dtype=torch.float32, device=imgs.device)
            # Targets: delta_xyz + gripper_open_cmd (0/1, or -1 mask if None)
            targ = torch.tensor([t.action_delta_xyz + ([float(t.action_gripper_open_cmd)] if t.action_gripper_open_cmd is not None else [0.0]) for t in batch],
                                dtype=torch.float32, device=imgs.device)
            gr_mask = torch.tensor([1.0 if t.action_gripper_open_cmd is not None else 0.0 for t in batch], dtype=torch.float32, device=imgs.device).unsqueeze(-1)

            pred = model(imgs, states)
            loss_xyz = F.mse_loss(pred[:, :3], targ[:, :3])
            loss_gr = ((pred[:, 3:4] - targ[:, 3:4]) ** 2 * gr_mask).mean()
            loss = loss_xyz + loss_gr

            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            losses.append(float(loss.detach().cpu().item()))

        print(f"[BC] epoch={ep+1}/{args.epochs} loss={np.mean(losses):.6f} n={len(trs)}")

    save_path = args.save_path.strip()
    if not save_path:
        save_path = os.path.join(os.path.abspath(args.run_dir), "bc_pick_place.pt")
    torch.save({"state_dim": state_dim, "img_size": int(args.img_size), "model": {"net": model.net.state_dict(), "head": model.head.state_dict()}}, save_path)
    print(f"[BC] saved {os.path.abspath(save_path)}")


if __name__ == "__main__":
    main()


