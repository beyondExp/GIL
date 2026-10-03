from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


def _optional_import_httpx():
    try:
        import httpx  # type: ignore

        return httpx
    except Exception:
        return None


@dataclass(frozen=True)
class OllamaConfig:
    host: str = "http://127.0.0.1:11434"
    model: str = "llama3.1"
    # First request can include model load; default higher to avoid timeouts.
    timeout_s: float = 120.0
    keep_alive: str = "10m"


class OllamaInstructionAgent:
    """
    Minimal agent that turns a natural-language instruction into a *local navigation goal*
    relative to a spawn pose.

    This is intentionally narrow: it lets us test the curriculum's instruction stages end-to-end
    without committing to a full VLA stack yet.
    """

    def __init__(self, cfg: OllamaConfig | None = None):
        self.cfg = cfg or OllamaConfig(
            host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1"),
            timeout_s=float(os.getenv("OLLAMA_TIMEOUT_S", "20.0")),
        )
        httpx = _optional_import_httpx()
        if httpx is None:
            raise RuntimeError("httpx not installed. Install with `pip install httpx`.")
        self._httpx = httpx

    def propose_goal_offset(self, *, instruction: str, context: dict[str, Any] | None = None) -> dict[str, float]:
        """
        Return a dict {dx, dy} in meters.
        """
        ctx = context or {}
        sys = (
            "You are a robot navigation planner. Convert the instruction into a small local goal offset.\n"
            "Return ONLY strict JSON with keys dx, dy (meters). Keep magnitudes <= 2.0.\n"
        )
        user = {
            "instruction": instruction,
            "context": ctx,
            "constraints": {"abs_dx_max": 2.0, "abs_dy_max": 2.0},
            "examples": [
                {"instruction": "go forward one meter", "dx": 1.0, "dy": 0.0},
                {"instruction": "move left 0.5m", "dx": 0.0, "dy": 0.5},
                {"instruction": "go to the right", "dx": 0.0, "dy": -1.0},
            ],
        }
        payload = {
            "model": self.cfg.model,
            "prompt": sys + "\n" + json.dumps(user, ensure_ascii=False),
            "stream": False,
            "format": "json",
            "keep_alive": self.cfg.keep_alive,
        }
        url = self.cfg.host.rstrip("/") + "/api/generate"
        with self._httpx.Client(timeout=self.cfg.timeout_s) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        txt = str(data.get("response") or "{}")
        out = json.loads(txt)
        dx = float(out.get("dx", 0.0))
        dy = float(out.get("dy", 0.0))
        dx = max(-2.0, min(2.0, dx))
        dy = max(-2.0, min(2.0, dy))
        return {"dx": dx, "dy": dy}


class OllamaDiscreteChoiceAgent:
    """
    Ask Ollama to choose one option from a discrete menu.

    This is the safest way to use an "untrained agent" to steer: it picks among bounded actions
    that GIL can execute safely.
    """

    def __init__(self, cfg: OllamaConfig | None = None):
        self.cfg = cfg or OllamaConfig(
            host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1"),
            timeout_s=float(os.getenv("OLLAMA_TIMEOUT_S", "20.0")),
        )
        httpx = _optional_import_httpx()
        if httpx is None:
            raise RuntimeError("httpx not installed. Install with `pip install httpx`.")
        self._httpx = httpx

    def choose(self, *, task: str, options: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Return strict JSON with:
          - choice: integer index in [0, len(options)-1]
          - action: string (optional; for callers that offer multiple action types)
          - reason: short string (optional)
        """
        ctx = context or {}
        sys = (
            "You are a robot navigation policy that must select from a discrete menu.\n"
            "Return ONLY strict JSON with keys: choice (int), action (string, optional), reason (string, optional).\n"
            "You MUST choose a valid index.\n"
        )
        user = {
            "task": task,
            "context": ctx,
            "options": options,
            "constraints": {"choice_min": 0, "choice_max": max(0, len(options) - 1)},
        }
        payload = {
            "model": self.cfg.model,
            "prompt": sys + "\n" + json.dumps(user, ensure_ascii=False),
            "stream": False,
            "format": "json",
            "keep_alive": self.cfg.keep_alive,
        }
        url = self.cfg.host.rstrip("/") + "/api/generate"
        with self._httpx.Client(timeout=self.cfg.timeout_s) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        txt = str(data.get("response") or "{}")
        out = json.loads(txt)
        # sanitize
        try:
            choice = int(out.get("choice", 0))
        except Exception:
            choice = 0
        choice = max(0, min(max(0, len(options) - 1), choice))
        action = str(out.get("action") or "goal")
        reason = str(out.get("reason") or "")
        return {"choice": choice, "action": action, "reason": reason}

