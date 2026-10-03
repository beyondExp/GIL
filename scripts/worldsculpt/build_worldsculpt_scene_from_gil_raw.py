#!/usr/bin/env python3
"""
Build a WorldSculpt GT scene directory from GIL's Isaac-side raw capture.

Input (scene_dir produced by /gil/gt_scene):
  <scene_dir>/_raw/meta.json
  <scene_dir>/_raw/rgb_npy/0000.npy        (H,W,3 uint8)
  <scene_dir>/_raw/instance_id_npy/0000.npy (H,W int64)
  <scene_dir>/_raw/bbox3d_json/0000.json    (Replicator bbox3d annotator raw dump)

Output (written into --out-scene-dir):
  0000.png ... RGB frames
  transforms.json (WorldSculpt schema: intrinsics, frames[].transform_matrix, instances[].aabb_world)
  masks/objNN/0000.png ... per-instance binary masks
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_png_u8(arr: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(str(path))


def _parse_ints(s: Any) -> list[int]:
    if s is None:
        return []
    if isinstance(s, list):
        try:
            return [int(x) for x in s]
        except Exception:
            return []
    txt = str(s)
    # Handle numpy-like "[1 2 3]" and python list-like "[1, 2, 3]"
    import re

    return [int(m) for m in re.findall(r"-?\d+", txt)]


def _bbox_items(raw: Any, *, bbox_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    Normalize common Replicator bbox3d annotator schemas into a list of dicts:
      { instanceId: int, aabb_min: [x,y,z], aabb_max: [x,y,z] }
    """
    if raw is None:
        return []
    # Special schema seen in some Isaac/Replicator builds:
    # - raw["data"] is a *string* repr of a list of tuples
    # - raw["info"]["bboxIds"] is a numpy-like string "[67 70 ...]"
    # - raw["info"]["primPaths"] aligns with bboxIds
    if isinstance(raw, dict) and isinstance(raw.get("data"), str) and isinstance(raw.get("info"), dict):
        info = raw.get("info") or {}
        bbox_ids = _parse_ints(info.get("bboxIds"))
        prim_paths = list(info.get("primPaths") or [])
        try:
            txt = str(raw["data"]).strip()
            # Replicator dumps sometimes omit commas between tuples, e.g. ")\n ("
            txt = txt.replace(")\n (", "),\n (").replace(")\r\n (", "),\r\n (")
            rows = ast.literal_eval(txt)
        except Exception:
            rows = []
        out: list[dict[str, Any]] = []
        for idx, row in enumerate(rows):
            try:
                xmin, ymin, zmin, xmax, ymax, zmax = [float(x) for x in row[1:7]]
                T = np.asarray(row[7], dtype=np.float64).reshape(4, 4)
                corners = np.array(
                    [[x, y, z, 1.0] for x in (xmin, xmax) for y in (ymin, ymax) for z in (zmin, zmax)],
                    dtype=np.float64,
                )
                wc = corners @ T
                mn = wc[:, :3].min(axis=0).tolist()
                mx = wc[:, :3].max(axis=0).tolist()
                bid = int(bbox_ids[idx]) if idx < len(bbox_ids) else int(idx)
                ppath = str(prim_paths[idx]) if idx < len(prim_paths) else ""
                out.append({"bboxId": bid, "primPath": ppath, "aabb_min": mn, "aabb_max": mx})
            except Exception:
                continue
        return out

    d = raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
    if d is None:
        return []

    if isinstance(d, list):
        out = []
        for it in d:
            if not isinstance(it, dict):
                continue
            iid = it.get("instanceId") or it.get("instance_id") or it.get("id")
            mn = it.get("aabb_min") or it.get("min")
            mx = it.get("aabb_max") or it.get("max")
            if iid is None or mn is None or mx is None:
                # Some schemas use corners: [[x,y,z] * 8]
                corners = it.get("corners") or it.get("corner_points") or it.get("points")
                if iid is None or corners is None:
                    continue
                c = np.asarray(corners, dtype=np.float64).reshape(-1, 3)
                mn = c.min(axis=0).tolist()
                mx = c.max(axis=0).tolist()
            try:
                out.append({"instanceId": int(iid), "aabb_min": mn, "aabb_max": mx})
            except Exception:
                continue
        return out

    if isinstance(d, dict):
        iids = d.get("instanceId") or d.get("instance_ids") or d.get("ids")
        mins = d.get("aabb_min") or d.get("min") or d.get("mins")
        maxs = d.get("aabb_max") or d.get("max") or d.get("maxs")
        if iids is not None and mins is not None and maxs is not None:
            out = []
            for iid, mn, mx in zip(iids, mins, maxs):
                out.append({"instanceId": int(iid), "aabb_min": mn, "aabb_max": mx})
            return out
    return []


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw-scene-dir", required=True, help="Scene dir produced by Isaac-side /gil/gt_scene exporter")
    p.add_argument("--out-scene-dir", required=True, help="Output WorldSculpt GT scene directory")
    p.add_argument("--max-instances", type=int, default=24, help="Pick up to K largest instances by pixel area")
    p.add_argument("--min-pixels", type=int, default=250, help="Ignore instances with fewer pixels than this")
    args = p.parse_args()

    raw_scene_dir = Path(args.raw_scene_dir).resolve()
    out_scene_dir = Path(args.out_scene_dir).resolve()
    raw_dir = raw_scene_dir / "_raw"
    meta_path = raw_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(str(meta_path))

    meta = _load_json(meta_path)
    frames = meta.get("frames") or []
    if not frames:
        raise ValueError("raw meta.json has no frames[]")

    rgb_dir = raw_dir / "rgb_npy"
    inst_dir = raw_dir / "instance_id_npy"
    bbox_dir = raw_dir / "bbox3d_json"
    inst_info = _load_json(raw_dir / "instance_info.json") if (raw_dir / "instance_info.json").exists() else {}
    bbox_info = _load_json(raw_dir / "bbox_info.json") if (raw_dir / "bbox_info.json").exists() else {}
    id_to_labels = (inst_info or {}).get("idToLabels") or {}
    # normalize keys to int
    try:
        id_to_labels = {int(k): str(v) for k, v in dict(id_to_labels).items()}
    except Exception:
        id_to_labels = {}

    # Pass 1: pick instances by accumulated pixel area (across frames).
    area: dict[int, int] = {}
    for fr in frames:
        i = int(fr.get("frame"))
        inst_path = inst_dir / f"{i:04d}.npy"
        if not inst_path.exists():
            continue
        inst = np.load(inst_path)
        uniq, counts = np.unique(inst, return_counts=True)
        for iid, c in zip(uniq.tolist(), counts.tolist()):
            iid = int(iid)
            if iid <= 0:
                continue
            if int(c) < int(args.min_pixels):
                continue
            area[iid] = int(area.get(iid, 0) + int(c))

    # Only keep ids that can be mapped to a primPath (for bbox association).
    eligible = [(iid, a) for (iid, a) in area.items() if int(iid) in id_to_labels]
    picked = sorted(eligible, key=lambda kv: -kv[1])[: max(0, int(args.max_instances))]
    iids = [int(k) for k, _ in picked]
    pass_index_by_iid = {iid: idx for idx, iid in enumerate(iids)}

    # Pass 2: write RGB frames and per-instance masks; aggregate AABBs from bbox json.
    aabb_by_pidx: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    aabb_by_prim: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    out_scene_dir.mkdir(parents=True, exist_ok=True)
    (out_scene_dir / "masks").mkdir(parents=True, exist_ok=True)

    out_frames = []
    for fr in frames:
        i = int(fr.get("frame"))
        stem = f"{i:04d}"

        rgb_path = rgb_dir / f"{stem}.npy"
        if rgb_path.exists():
            rgb = np.load(rgb_path)
            _save_png_u8(rgb.astype(np.uint8, copy=False), out_scene_dir / f"{stem}.png")

        inst_path = inst_dir / f"{stem}.npy"
        inst = np.load(inst_path) if inst_path.exists() else None
        if inst is not None and iids:
            for iid in iids:
                pidx = pass_index_by_iid[iid]
                mask = (inst == iid).astype(np.uint8) * 255
                if int(mask.sum()) == 0:
                    continue
                _save_png_u8(mask, out_scene_dir / "masks" / f"obj{pidx:02d}" / f"{stem}.png")

        bbox_path = bbox_dir / f"{stem}.json"
        if bbox_path.exists():
            raw_bbox = _load_json(bbox_path)
            for it in _bbox_items(raw_bbox, bbox_info=bbox_info):
                mn = np.asarray(it["aabb_min"], dtype=np.float64).reshape(3)
                mx = np.asarray(it["aabb_max"], dtype=np.float64).reshape(3)
                if not np.isfinite(mn).all() or not np.isfinite(mx).all():
                    continue
                ppath = str(it.get("primPath") or "")
                if ppath:
                    if ppath not in aabb_by_prim:
                        aabb_by_prim[ppath] = (mn.copy(), mx.copy())
                    else:
                        a0, a1 = aabb_by_prim[ppath]
                        aabb_by_prim[ppath] = (np.minimum(a0, mn), np.maximum(a1, mx))

        out_frames.append({"file_path": f"{stem}.png", "transform_matrix": fr["transform_matrix"]})

    instances = []
    for iid, pidx in sorted(pass_index_by_iid.items(), key=lambda kv: kv[1]):
        ppath = id_to_labels.get(int(iid), "")
        if not ppath:
            continue
        if ppath not in aabb_by_prim:
            continue
        mn, mx = aabb_by_prim[ppath]
        center = ((mn + mx) * 0.5).tolist()
        instances.append(
            {
                "pass_index": int(pidx),
                "file_identifier": f"iid_{int(iid)}",
                "aabb_world": [mn.tolist(), mx.tolist()],
                "center_world": center,
                "prim_path": ppath,
            }
        )

    out_meta: dict[str, Any] = {
        "camera_model": str(meta.get("camera_model") or "OPENGL"),
        "w": float(meta["w"]),
        "h": float(meta["h"]),
        "fl_x": float(meta["fl_x"]),
        "fl_y": float(meta["fl_y"]),
        "cx": float(meta["cx"]),
        "cy": float(meta["cy"]),
        "frames": out_frames,
        "instances": instances,
    }
    (out_scene_dir / "transforms.json").write_text(json.dumps(out_meta, indent=2), encoding="utf-8")

    print(f"[ok] wrote scene: {out_scene_dir}")
    print(f"[ok] frames={len(out_frames)} instances={len(instances)} picked_iids={len(iids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

