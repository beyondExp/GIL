from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ImageGateReport:
    ok: bool
    reason: str
    stats: dict[str, Any]


def _decode_data_url(data_url: str) -> bytes | None:
    if not isinstance(data_url, str) or not data_url.startswith("data:image"):
        return None
    if "," not in data_url:
        return None
    try:
        _header, b64 = data_url.split(",", 1)
        return base64.b64decode(b64)
    except Exception:
        return None


def image_gate(
    data_url: str | None,
    *,
    min_std: float = 6.0,
    min_mean: float = 8.0,
    max_mean: float = 247.0,
    min_w: int = 80,
    min_h: int = 45,
) -> ImageGateReport:
    """Heuristic check for 'usable' camera frames.

    We gate out:
    - missing images
    - nearly-uniform frames (all black/white/sky) via low grayscale std-dev
    - tiny frames
    """

    if not data_url:
        return ImageGateReport(False, "missing", {"missing": True})
    raw = _decode_data_url(str(data_url))
    if not raw:
        return ImageGateReport(False, "decode_failed", {})

    try:
        from PIL import Image
        import numpy as np
    except Exception as exc:  # pragma: no cover
        return ImageGateReport(False, "pillow_unavailable", {"error": repr(exc)})

    try:
        im = Image.open(io.BytesIO(raw)).convert("L")  # grayscale
        w, h = im.size
        if w < int(min_w) or h < int(min_h):
            return ImageGateReport(False, "too_small", {"w": w, "h": h})
        arr = np.asarray(im, dtype="float32")
        mean = float(arr.mean())
        std = float(arr.std())
        stats = {"w": int(w), "h": int(h), "mean": mean, "std": std}
        if mean < float(min_mean):
            return ImageGateReport(False, "too_dark", stats)
        if mean > float(max_mean):
            return ImageGateReport(False, "too_bright", stats)
        if std < float(min_std):
            return ImageGateReport(False, "too_uniform", stats)
        return ImageGateReport(True, "ok", stats)
    except Exception as exc:
        return ImageGateReport(False, "decode_error", {"error": repr(exc)})

