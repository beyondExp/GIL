from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from gil.core.logging_setup import get_logger

log = get_logger("perception")


class PerceptionClient:
    """Optional client for gil_models. Failures never authorize motion."""

    def __init__(self, base_url: str = "", timeout_s: float = 2.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = timeout_s

    def analyze(self, observation: dict[str, Any]) -> dict[str, Any]:
        image = observation.get("last_image") or observation.get("image")
        if not image:
            return {"ok": False, "reason": "no_image", "safe_for_motion_authority": False}
        if not self.base_url:
            return {
                "ok": True,
                "backend": "offline",
                "objects": list(observation.get("objects") or []),
                "safe_for_motion_authority": False,
                "note": "No GIL_MODELS_URL; using objects already in observation.",
            }
        payload = json.dumps({"image": image, "prompt": "list objects with poses"}).encode("utf-8")
        req = Request(
            f"{self.base_url}/analyze_scene",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            log.warning("perception unavailable: %s", exc)
            return {"ok": False, "reason": str(exc), "safe_for_motion_authority": False}
        if isinstance(body, dict):
            body.setdefault("safe_for_motion_authority", False)
            body["ok"] = True
            return body
        return {"ok": False, "reason": "invalid_response", "safe_for_motion_authority": False}

    def enrich(self, observation: dict[str, Any]) -> dict[str, Any]:
        out = dict(observation)
        vision = self.analyze(observation)
        out["perception"] = vision
        if vision.get("ok") and vision.get("objects") and not out.get("objects"):
            out["objects"] = vision["objects"]
        return out
