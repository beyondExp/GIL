#!/usr/bin/env python3
"""
Demo "converted environment" builder: turn GIL raw GT capture into a colored pointcloud.

This does NOT require WorldSculpt weights. It creates a visually convincing artifact for demos:
  - extracts oriented boxes from Replicator bbox3d dumps
  - densely samples box surfaces into a point cloud (rich + stable)
  - optional: colorize by projecting into RGB frames (best-effort)

Output `.npz` schema (for Isaac loader):
  - points_xyz: (N,3) float32
  - colors_rgb: (N,3) uint8
"""

from __future__ import annotations

import argparse
import ast
import math
import json
from pathlib import Path
from typing import Any

import numpy as np


BLENDER_OPENCV = np.diag([1.0, -1.0, -1.0, 1.0]).astype(np.float64)


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _parse_ints(txt: Any) -> list[int]:
    if txt is None:
        return []
    if isinstance(txt, list):
        return [int(x) for x in txt]
    import re

    return [int(m) for m in re.findall(r"-?\d+", str(txt))]


def _parse_bbox3d_rows(bbox_json: dict[str, Any]) -> list[tuple[str, np.ndarray, tuple[float, float, float, float, float, float]]]:
    """
    Parse the Replicator bbox3d dump schema we see in this repo:
      bbox_json["data"] is a string representing a list of tuples, but may omit commas between tuples.
      bbox_json["info"]["bboxIds"] aligns with bbox_json["info"]["primPaths"].

    Returns list of (primPath, T_4x4, (xmin,ymin,zmin,xmax,ymax,zmax)) where T uses the observed
    row-vector convention (points row-vectors multiplied by T).
    """
    if not isinstance(bbox_json, dict):
        return []
    if not isinstance(bbox_json.get("data"), str):
        return []
    info = bbox_json.get("info") or {}
    prim_paths = list(info.get("primPaths") or [])
    # make tuple list parsable
    txt = str(bbox_json["data"]).strip()
    txt = txt.replace(")\n (", "),\n (").replace(")\r\n (", "),\r\n (")
    try:
        rows = ast.literal_eval(txt)
    except Exception:
        return []

    out: list[tuple[str, np.ndarray, tuple[float, float, float, float, float, float]]] = []
    for idx, row in enumerate(rows):
        if idx >= len(prim_paths):
            break
        ppath = str(prim_paths[idx])
        try:
            xmin, ymin, zmin, xmax, ymax, zmax = [float(x) for x in row[1:7]]
            T = np.asarray(row[7], dtype=np.float64).reshape(4, 4)
        except Exception:
            continue
        out.append((ppath, T, (xmin, ymin, zmin, xmax, ymax, zmax)))
    return out


def _sample_box_surface_local(bounds: tuple[float, float, float, float, float, float], *, step: float) -> np.ndarray:
    """Dense points on the surface of an axis-aligned box in local coords."""
    xmin, ymin, zmin, xmax, ymax, zmax = bounds
    sx = max(step, float(abs(xmax - xmin)))
    sy = max(step, float(abs(ymax - ymin)))
    sz = max(step, float(abs(zmax - zmin)))
    # Use at least 2 samples per axis
    nx = max(2, int(math.ceil(abs(xmax - xmin) / step)) + 1)
    ny = max(2, int(math.ceil(abs(ymax - ymin) / step)) + 1)
    nz = max(2, int(math.ceil(abs(zmax - zmin) / step)) + 1)
    xs = np.linspace(xmin, xmax, nx, dtype=np.float64)
    ys = np.linspace(ymin, ymax, ny, dtype=np.float64)
    zs = np.linspace(zmin, zmax, nz, dtype=np.float64)
    pts = []
    # 6 faces
    yy, zz = np.meshgrid(ys, zs)
    pts.append(np.stack([np.full_like(yy, xmin), yy, zz], axis=-1).reshape(-1, 3))
    pts.append(np.stack([np.full_like(yy, xmax), yy, zz], axis=-1).reshape(-1, 3))
    xx, zz = np.meshgrid(xs, zs)
    pts.append(np.stack([xx, np.full_like(xx, ymin), zz], axis=-1).reshape(-1, 3))
    pts.append(np.stack([xx, np.full_like(xx, ymax), zz], axis=-1).reshape(-1, 3))
    xx, yy = np.meshgrid(xs, ys)
    pts.append(np.stack([xx, yy, np.full_like(xx, zmin)], axis=-1).reshape(-1, 3))
    pts.append(np.stack([xx, yy, np.full_like(xx, zmax)], axis=-1).reshape(-1, 3))
    out = np.concatenate(pts, axis=0)
    # Deduplicate (edges/corners repeated)
    out = np.unique(out.round(6), axis=0)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw-scene-dir", required=True, help="assets/worldsculpt/input/<run_id>")
    p.add_argument("--out-npz", default="", help="Output .npz path (default: <raw_scene_dir>/converted_env/pointcloud.npz)")
    p.add_argument("--max-frames", type=int, default=12, help="Max frames to use from capture.")
    p.add_argument("--surface-step", type=float, default=0.06, help="Surface sampling step in meters (smaller = denser).")
    p.add_argument("--include-prefix", action="append", default=["/World/Maze/"], help="Prim path prefix(es) to include. Can be repeated.")
    p.add_argument("--include-all", action="store_true", default=False, help="Include all prims from bbox3d (ignore prefixes).")
    p.add_argument("--color", choices=["semantic", "rgb"], default="semantic", help="Point color mode.")
    p.add_argument("--rgb-frame", type=int, default=0, help="Which frame index to use for RGB projection (0-based within capture).")
    p.add_argument("--max-points", type=int, default=200_000, help="Randomly downsample to at most this many points (0 = no limit).")
    args = p.parse_args()

    raw_scene_dir = Path(args.raw_scene_dir).resolve()
    raw_dir = raw_scene_dir / "_raw"
    meta = _load_json(raw_dir / "meta.json")
    frames = list(meta.get("frames") or [])[: int(args.max_frames)]

    include_prefixes = [] if bool(args.include_all) else [str(p) for p in (args.include_prefix or []) if str(p)]
    # Aggregate box rows across frames; keep the latest transform per prim and merge bounds in local.
    boxes: dict[str, tuple[np.ndarray, tuple[float, float, float, float, float, float]]] = {}
    for fr in frames:
        i = int(fr.get("frame"))
        bj = raw_dir / "bbox3d_json" / f"{i:04d}.json"
        if not bj.exists():
            continue
        rows = _parse_bbox3d_rows(_load_json(bj))
        for ppath, T, bounds in rows:
            if include_prefixes and not any(str(ppath).startswith(pref) for pref in include_prefixes):
                continue
            # Keep latest T; bounds are stable for our maze cubes.
            boxes[ppath] = (T, bounds)

    if not boxes:
        print("[warn] no bbox3d boxes found; pointcloud will be empty.", flush=True)

    # Sample surfaces (semantic colors first)
    pts_all = []
    col_all = []
    step = float(args.surface_step)
    for ppath, (T, bounds) in boxes.items():
        local_pts = _sample_box_surface_local(bounds, step=step)  # (M,3)
        # to homogeneous row vectors
        h = np.concatenate([local_pts, np.ones((local_pts.shape[0], 1), dtype=np.float64)], axis=1)
        world = (h @ T)[:, :3]
        pts_all.append(world.astype(np.float32))
        # simple semantic color palette (maze walls light gray)
        if str(ppath).startswith("/World/Maze/"):
            c = np.tile(np.array([[210, 210, 210]], dtype=np.uint8), (world.shape[0], 1))
        else:
            c = np.tile(np.array([[120, 200, 255]], dtype=np.uint8), (world.shape[0], 1))
        col_all.append(c)

    pts = np.concatenate(pts_all, axis=0) if pts_all else np.zeros((0, 3), dtype=np.float32)
    cols = np.concatenate(col_all, axis=0) if col_all else np.zeros((0, 3), dtype=np.uint8)

    # Optional: RGB projection for colorization (best-effort, uses one frame for speed).
    if str(args.color).lower().strip() == "rgb" and pts.shape[0] > 0:
        try:
            frames_all = list(meta.get("frames") or [])
            fi = int(max(0, min(len(frames_all) - 1, int(args.rgb_frame))))
            fr = frames_all[fi]
            rgb_p = raw_dir / "rgb_npy" / f"{int(fr.get('frame')):04d}.npy"
            if rgb_p.exists():
                rgb = np.load(rgb_p)  # H,W,3 uint8
                h_img, w_img = int(rgb.shape[0]), int(rgb.shape[1])
                fx = float(meta["fl_x"])
                fy = float(meta["fl_y"])
                cx = float(meta["cx"])
                cy = float(meta["cy"])

                c2w_bl = np.asarray(fr["transform_matrix"], dtype=np.float64).reshape(4, 4)
                c2w_cv = c2w_bl @ BLENDER_OPENCV
                w2c_cv = np.linalg.inv(c2w_cv)

                ph = np.concatenate([pts.astype(np.float64), np.ones((pts.shape[0], 1), dtype=np.float64)], axis=1)
                pc = ph @ w2c_cv
                z = pc[:, 2]
                valid = z > 1e-3
                x = pc[:, 0]
                y = pc[:, 1]
                u = fx * (x / z) + cx
                v = fy * (y / z) + cy
                valid &= (u >= 0.0) & (u < float(w_img - 1)) & (v >= 0.0) & (v < float(h_img - 1))
                ui = u[valid].astype(np.int32)
                vi = v[valid].astype(np.int32)
                cols_rgb = cols.copy()
                cols_rgb[valid] = rgb[vi, ui]
                cols = cols_rgb
        except Exception:
            pass

    # Downsample for rendering performance if needed
    try:
        mp = int(args.max_points)
    except Exception:
        mp = 200_000
    if mp and mp > 0 and pts.shape[0] > mp:
        rng = np.random.default_rng(0)
        idx = rng.choice(pts.shape[0], size=mp, replace=False)
        pts = pts[idx]
        cols = cols[idx]

    out_npz = Path(args.out_npz).resolve() if args.out_npz else (raw_scene_dir / "converted_env" / "pointcloud.npz")
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_npz, points_xyz=pts, colors_rgb=cols)
    print(f"[ok] wrote {out_npz} points={int(pts.shape[0])} prims={len(boxes)} step={step} color={args.color}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

