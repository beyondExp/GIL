from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _parse_data_url(data_url: str) -> tuple[str, bytes] | None:
    if not isinstance(data_url, str) or not data_url.startswith("data:image"):
        return None
    if "," not in data_url:
        return None
    header, b64 = data_url.split(",", 1)
    # header example: data:image/jpeg;base64
    mime = "image/jpeg"
    try:
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0].strip() or mime
    except Exception:
        mime = "image/jpeg"
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    return mime, raw


def _ext_for_mime(mime: str) -> str:
    m = (mime or "").lower().strip()
    if "png" in m:
        return "png"
    if "webp" in m:
        return "webp"
    return "jpg"


@dataclass
class RecorderStatus:
    active: bool
    run_id: str | None = None
    robot_kind: str | None = None
    root_dir: str | None = None
    frames: int = 0
    last_frame_at_ms: int | None = None


class EpisodeRecorder:
    """Lightweight recorder for ObservationEnvelope-like data.

    Records:
    - images (data URLs) to disk
    - one JSONL line per recorded tick with pose + paths + camera_info
    """

    def __init__(self, *, root_dir: str, sample_period_ms: int = 250) -> None:
        self._root_dir = os.path.abspath(root_dir)
        self._sample_period_ms = int(max(50, sample_period_ms))
        _safe_mkdir(self._root_dir)

        self._active = False
        self._run_id: str | None = None
        self._robot_kind: str | None = None
        self._run_dir: str | None = None
        self._frames_dir: str | None = None
        self._jsonl_path: str | None = None
        self._meta_path: str | None = None
        self._calib_path: str | None = None
        self._meta_written: bool = False
        self._last_saved_at_ms: int = 0
        self._frames: int = 0

    def status(self) -> RecorderStatus:
        return RecorderStatus(
            active=self._active,
            run_id=self._run_id,
            robot_kind=self._robot_kind,
            root_dir=self._run_dir,
            frames=int(self._frames),
            last_frame_at_ms=(self._last_saved_at_ms or None),
        )

    def start(self, *, robot_kind: str, run_id: str | None = None) -> RecorderStatus:
        rid = (run_id or "").strip() or uuid.uuid4().hex[:12]
        kind = (robot_kind or "").strip().lower() or "humanoid"
        run_dir = os.path.join(self._root_dir, rid)
        frames_dir = os.path.join(run_dir, "frames")
        _safe_mkdir(frames_dir)
        jsonl = os.path.join(run_dir, "trajectory.jsonl")
        meta = os.path.join(run_dir, "meta.json")
        calib = os.path.join(run_dir, "calibration.json")

        self._active = True
        self._run_id = rid
        self._robot_kind = kind
        self._run_dir = run_dir
        self._frames_dir = frames_dir
        self._jsonl_path = jsonl
        self._meta_path = meta
        self._calib_path = calib
        self._meta_written = False
        self._last_saved_at_ms = 0
        self._frames = 0
        try:
            with open(meta, "w", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "run_id": rid,
                            "robot_kind": kind,
                            "started_at_ms": _now_ms(),
                            "sample_period_ms": int(self._sample_period_ms),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
        except Exception:
            pass
        return self.status()

    def stop(self) -> RecorderStatus:
        st = self.status()
        self._active = False
        self._run_id = None
        self._robot_kind = None
        self._run_dir = None
        self._frames_dir = None
        self._jsonl_path = None
        self._meta_path = None
        self._calib_path = None
        self._meta_written = False
        self._last_saved_at_ms = 0
        self._frames = 0
        return st

    def maybe_record(self, *, robot_kind: str, state: dict[str, Any]) -> None:
        if not self._active:
            return
        if (self._robot_kind or "").strip().lower() and (robot_kind or "").strip().lower() != (self._robot_kind or ""):
            return

        updated_at_ms = 0
        try:
            updated_at_ms = int(((state.get("meta") or {}).get("updated_at_ms")) or 0)
        except Exception:
            updated_at_ms = 0
        if updated_at_ms <= 0:
            updated_at_ms = _now_ms()

        if self._last_saved_at_ms and (updated_at_ms - self._last_saved_at_ms) < self._sample_period_ms:
            return

        frames_dir = self._frames_dir
        jsonl_path = self._jsonl_path
        meta_path = self._meta_path
        calib_path = self._calib_path
        if not frames_dir or not jsonl_path:
            return

        base = (state.get("base") or {}) if isinstance(state.get("base"), dict) else {}
        images: dict[str, Any] = {}
        try:
            images = dict(state.get("images") or {}) if isinstance(state.get("images"), dict) else {}
        except Exception:
            images = {}

        # Normalize candidate images.
        candidates: dict[str, str] = {}
        for k in ("last_image", "last_image_wide", "last_image_left", "last_image_right"):
            v = state.get(k)
            if isinstance(v, str) and v.startswith("data:image"):
                candidates[k] = v
        # Also accept images dict entries (if backend uses it).
        for k, v in (images or {}).items():
            if isinstance(v, str) and v.startswith("data:image"):
                candidates[f"images.{k}"] = v

        saved: dict[str, str] = {}
        for name, data_url in candidates.items():
            parsed = _parse_data_url(data_url)
            if not parsed:
                continue
            mime, raw = parsed
            ext = _ext_for_mime(mime)
            fn = f"{updated_at_ms}_{name.replace('.', '_')}.{ext}"
            path = os.path.join(frames_dir, fn)
            try:
                with open(path, "wb") as fh:
                    fh.write(raw)
                saved[name] = os.path.relpath(path, os.path.dirname(jsonl_path)).replace("\\", "/")
            except Exception:
                continue

        if (not self._meta_written) and meta_path and calib_path:
            try:
                calib_obj = {
                    "recorded_at_ms": updated_at_ms,
                    "camera_info": state.get("camera_info"),
                    "camera_info_wide": state.get("camera_info_wide"),
                    "frame_graph": state.get("frame_graph"),
                    "world_anchor": state.get("world_anchor"),
                }
                with open(calib_path, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(calib_obj, ensure_ascii=False, indent=2) + "\n")
                self._meta_written = True
            except Exception:
                pass

        rec = {
            "t_ms": updated_at_ms,
            "robot_kind": robot_kind,
            "backend": str(state.get("backend") or ""),
            "mode": str(state.get("mode") or ""),
            "world_anchor": state.get("world_anchor"),
            "frame_graph": state.get("frame_graph"),
            "base": {
                "x": float(base.get("x", 0.0) or 0.0),
                "y": float(base.get("y", 0.0) or 0.0),
                "z": float(base.get("z", 0.0) or 0.0),
                "yaw": float(base.get("yaw", 0.0) or 0.0),
            },
            "camera_info": state.get("camera_info"),
            "camera_info_wide": state.get("camera_info_wide"),
            "frames": saved,
        }
        try:
            with open(jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            return

        self._frames += 1
        self._last_saved_at_ms = updated_at_ms

