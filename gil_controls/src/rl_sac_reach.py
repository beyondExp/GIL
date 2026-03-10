import math
import time
from dataclasses import dataclass

import numpy as np


try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except Exception:  # pragma: no cover
    torch = None
    nn = None
    F = None


@dataclass
class SACConfig:
    obs_dim: int = 6  # ee(xyz) + goal(xyz)
    act_dim: int = 3  # delta xyz
    hidden: int = 256
    gamma: float = 0.99
    tau: float = 0.005
    lr: float = 3e-4
    alpha_lr: float = 3e-4
    batch_size: int = 256
    replay_size: int = 200_000
    start_steps: int = 2_000
    update_after: int = 2_000
    update_every: int = 1
    policy_delay: int = 1
    target_entropy: float = -3.0  # -act_dim


class ReplayBuffer:
    def __init__(self, obs_dim: int, act_dim: int, size: int):
        self.obs = np.zeros((size, obs_dim), dtype=np.float32)
        self.next_obs = np.zeros((size, obs_dim), dtype=np.float32)
        self.acts = np.zeros((size, act_dim), dtype=np.float32)
        self.rews = np.zeros((size,), dtype=np.float32)
        self.done = np.zeros((size,), dtype=np.float32)
        self.max_size = int(size)
        self.ptr = 0
        self.size = 0

    def add(self, obs, act, rew, next_obs, done):
        self.obs[self.ptr] = obs
        self.acts[self.ptr] = act
        self.rews[self.ptr] = rew
        self.next_obs[self.ptr] = next_obs
        self.done[self.ptr] = float(done)
        self.ptr = (self.ptr + 1) % self.max_size
        self.size = min(self.size + 1, self.max_size)

    def sample(self, batch_size: int):
        idx = np.random.randint(0, self.size, size=batch_size)
        return dict(
            obs=self.obs[idx],
            acts=self.acts[idx],
            rews=self.rews[idx],
            next_obs=self.next_obs[idx],
            done=self.done[idx],
        )


LOG_STD_MIN = -20
LOG_STD_MAX = 2


class MLP(nn.Module):
    def __init__(self, in_dim: int, out_dim: int, hidden: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, out_dim),
        )

    def forward(self, x):
        return self.net(x)


class Actor(nn.Module):
    def __init__(self, obs_dim: int, act_dim: int, hidden: int):
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.mu = nn.Linear(hidden, act_dim)
        self.log_std = nn.Linear(hidden, act_dim)

    def forward(self, obs):
        h = self.trunk(obs)
        mu = self.mu(h)
        log_std = self.log_std(h)
        log_std = torch.clamp(log_std, LOG_STD_MIN, LOG_STD_MAX)
        return mu, log_std

    def sample(self, obs):
        mu, log_std = self(obs)
        std = torch.exp(log_std)
        eps = torch.randn_like(mu)
        pre_tanh = mu + eps * std
        a = torch.tanh(pre_tanh)
        # Tanh Gaussian log prob
        logp = -0.5 * (((pre_tanh - mu) / (std + 1e-8)) ** 2 + 2 * log_std + math.log(2 * math.pi))
        logp = logp.sum(dim=-1, keepdim=True)
        # Change of variables for tanh
        logp -= torch.log(1 - a.pow(2) + 1e-6).sum(dim=-1, keepdim=True)
        return a, logp


class Critic(nn.Module):
    def __init__(self, obs_dim: int, act_dim: int, hidden: int):
        super().__init__()
        self.q = MLP(obs_dim + act_dim, 1, hidden)

    def forward(self, obs, act):
        x = torch.cat([obs, act], dim=-1)
        return self.q(x)


def soft_update(target: nn.Module, source: nn.Module, tau: float):
    with torch.no_grad():
        for tp, sp in zip(target.parameters(), source.parameters()):
            tp.data.mul_(1 - tau).add_(tau * sp.data)


def torch_required():
    if torch is None:
        raise RuntimeError(
            "PyTorch is required for SAC. Install it (e.g. `pip install torch`) and restart gil_controls."
        )


def make_goal(rng: np.random.RandomState):
    x = float(rng.uniform(0.7, 1.6))
    y = float(rng.uniform(0.08, 0.6))
    z = float(rng.uniform(-0.6, 0.6))
    return np.array([x, y, z], dtype=np.float32)


def build_obs(ee_xyz: np.ndarray, goal_xyz: np.ndarray):
    return np.concatenate([ee_xyz.astype(np.float32), goal_xyz.astype(np.float32)], axis=0)



