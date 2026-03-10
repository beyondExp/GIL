import math
from dataclasses import dataclass
from typing import Optional, Tuple


def _torch_required():
    try:
        import torch  # noqa: F401
    except Exception as e:
        raise RuntimeError("PyTorch is required. Install deps from requirements-ml.txt") from e


def sinusoidal_time_embedding(t, dim: int):
    """t: (B,) int64/float tensor -> (B, dim)"""
    import torch

    half = dim // 2
    freqs = torch.exp(
        torch.arange(half, device=t.device, dtype=torch.float32) * (-math.log(10_000.0) / max(1, half - 1))
    )
    args = t.float().unsqueeze(-1) * freqs.unsqueeze(0)
    emb = torch.cat([torch.sin(args), torch.cos(args)], dim=-1)
    if dim % 2 == 1:
        emb = torch.cat([emb, torch.zeros((emb.shape[0], 1), device=t.device, dtype=emb.dtype)], dim=-1)
    return emb


@dataclass
class DiffusionConfig:
    action_dim: int = 3
    chunk_len: int = 16
    img_size: int = 128
    state_dim: int = 11  # ee(3)+cube(3)+bin(3)+gripper_open+held
    cond_dim: int = 256
    time_dim: int = 128
    hidden: int = 512
    diffusion_steps: int = 50
    beta_start: float = 1e-4
    beta_end: float = 2e-2


class ImageEncoder:
    def __init__(self, img_size: int, out_dim: int):
        _torch_required()
        import torch.nn as nn

        self.img_size = int(img_size)
        self.net = nn.Sequential(
            nn.Conv2d(3, 32, 5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(32, 64, 5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(64, 128, 5, stride=2, padding=2),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
            nn.Flatten(),
            nn.Linear(128 * 4 * 4, out_dim),
            nn.ReLU(),
        )

    def parameters(self):
        return self.net.parameters()

    def to(self, device):
        self.net.to(device)
        return self

    def train(self):
        self.net.train()

    def eval(self):
        self.net.eval()

    def __call__(self, img):
        return self.net(img)


class CondEncoder:
    def __init__(self, cfg: DiffusionConfig):
        _torch_required()
        import torch.nn as nn

        self.img_enc = ImageEncoder(cfg.img_size, cfg.cond_dim)
        self.state_enc = nn.Sequential(
            nn.Linear(cfg.state_dim, cfg.cond_dim),
            nn.ReLU(),
            nn.Linear(cfg.cond_dim, cfg.cond_dim),
            nn.ReLU(),
        )
        self.fuse = nn.Sequential(
            nn.Linear(cfg.cond_dim * 2, cfg.cond_dim),
            nn.ReLU(),
        )

    def parameters(self):
        return list(self.img_enc.parameters()) + list(self.state_enc.parameters()) + list(self.fuse.parameters())

    def to(self, device):
        self.img_enc.to(device)
        self.state_enc.to(device)
        self.fuse.to(device)
        return self

    def train(self):
        self.img_enc.train()
        self.state_enc.train()
        self.fuse.train()

    def eval(self):
        self.img_enc.eval()
        self.state_enc.eval()
        self.fuse.eval()

    def __call__(self, img, state_vec):
        import torch

        i = self.img_enc(img)
        s = self.state_enc(state_vec)
        return self.fuse(torch.cat([i, s], dim=-1))


class DenoiseMLP:
    """Predict noise eps for flattened action chunk, conditioned on (cond, t_emb)."""

    def __init__(self, cfg: DiffusionConfig):
        _torch_required()
        import torch.nn as nn

        self.cfg = cfg
        in_dim = cfg.chunk_len * cfg.action_dim + cfg.cond_dim + cfg.time_dim
        out_dim = cfg.chunk_len * cfg.action_dim
        self.net = nn.Sequential(
            nn.Linear(in_dim, cfg.hidden),
            nn.ReLU(),
            nn.Linear(cfg.hidden, cfg.hidden),
            nn.ReLU(),
            nn.Linear(cfg.hidden, cfg.hidden),
            nn.ReLU(),
            nn.Linear(cfg.hidden, out_dim),
        )

    def parameters(self):
        return self.net.parameters()

    def to(self, device):
        self.net.to(device)
        return self

    def train(self):
        self.net.train()

    def eval(self):
        self.net.eval()

    def __call__(self, x_noisy, t, cond):
        import torch

        # x_noisy: (B, L, D)
        b = x_noisy.shape[0]
        x = x_noisy.reshape(b, -1)
        t_emb = sinusoidal_time_embedding(t, self.cfg.time_dim)
        inp = torch.cat([x, cond, t_emb], dim=-1)
        eps = self.net(inp).reshape(b, self.cfg.chunk_len, self.cfg.action_dim)
        return eps


class GaussianDiffusion:
    def __init__(self, cfg: DiffusionConfig):
        _torch_required()
        import torch

        self.cfg = cfg
        self.T = int(cfg.diffusion_steps)
        betas = torch.linspace(float(cfg.beta_start), float(cfg.beta_end), self.T, dtype=torch.float32)
        alphas = 1.0 - betas
        alphas_cumprod = torch.cumprod(alphas, dim=0)
        self.register = {
            "betas": betas,
            "alphas": alphas,
            "alphas_cumprod": alphas_cumprod,
            "sqrt_alphas_cumprod": torch.sqrt(alphas_cumprod),
            "sqrt_one_minus_alphas_cumprod": torch.sqrt(1.0 - alphas_cumprod),
        }

    def to(self, device):
        import torch

        for k, v in list(self.register.items()):
            self.register[k] = v.to(device)
        return self

    def q_sample(self, x0, t, noise):
        """Forward diffusion: x_t = sqrt(a_bar)*x0 + sqrt(1-a_bar)*noise"""
        import torch

        b = x0.shape[0]
        a = self.register["sqrt_alphas_cumprod"][t].reshape(b, 1, 1)
        om = self.register["sqrt_one_minus_alphas_cumprod"][t].reshape(b, 1, 1)
        return a * x0 + om * noise

    def p_sample(self, model: DenoiseMLP, x_t, t, cond):
        """One reverse step."""
        import torch

        b = x_t.shape[0]
        betas_t = self.register["betas"][t].reshape(b, 1, 1)
        alphas_t = self.register["alphas"][t].reshape(b, 1, 1)
        a_bar = self.register["alphas_cumprod"][t].reshape(b, 1, 1)
        eps = model(x_t, t, cond)

        # Predict x0
        x0 = (x_t - torch.sqrt(1.0 - a_bar) * eps) / torch.sqrt(a_bar + 1e-8)
        # Compute mean of p(x_{t-1} | x_t)
        mean = (1.0 / torch.sqrt(alphas_t + 1e-8)) * (x_t - (betas_t / torch.sqrt(1.0 - a_bar + 1e-8)) * eps)
        if int(t[0].item()) == 0:
            return mean, x0
        noise = torch.randn_like(x_t)
        var = betas_t
        return mean + torch.sqrt(var) * noise, x0

    def sample(self, model: DenoiseMLP, cond, device, batch_size: int = 1):
        import torch

        x = torch.randn((batch_size, self.cfg.chunk_len, self.cfg.action_dim), device=device, dtype=torch.float32)
        for ti in reversed(range(self.T)):
            t = torch.full((batch_size,), ti, device=device, dtype=torch.long)
            x, _x0 = self.p_sample(model, x, t, cond)
        return x


