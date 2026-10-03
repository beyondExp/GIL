#!/usr/bin/env python3
"""
Converter/validator for GIL's Isaac GT export -> WorldSculpt GT scene contract.

In current GIL, the exporter already writes a WorldSculpt-compatible layout.
This tool exists to:
  - validate required files/fields exist,
  - optionally normalize frame naming,
  - print the resolved scene_dir to use as WorldSculpt INPUT_ROOT/<scene>.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scene-dir", required=True, help="Path produced by /gil/gt_scene exporter (contains transforms.json)")
    p.add_argument("--out-dir", default="", help="Optional destination dir (copy). If empty, validates in-place.")
    p.add_argument("--normalize-frames", action="store_true", help="Rename frames in-place to 0000.png, 0001.png... and update transforms.json")
    args = p.parse_args()

    src = Path(args.scene_dir).resolve()
    if not src.exists():
        raise FileNotFoundError(str(src))
    if not (src / "transforms.json").exists():
        raise FileNotFoundError(str(src / "transforms.json"))

    dst = Path(args.out_dir).resolve() if args.out_dir else src
    if args.out_dir:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    meta = json.loads((dst / "transforms.json").read_text(encoding="utf-8"))
    for k in ("w", "h", "fl_x", "fl_y", "cx", "cy", "frames", "instances"):
        if k not in meta:
            raise ValueError(f"transforms.json missing required key: {k}")
    frames = meta["frames"]
    if not isinstance(frames, list) or not frames:
        raise ValueError("transforms.json frames[] missing/empty")
    instances = meta["instances"]
    if not isinstance(instances, list):
        raise ValueError("transforms.json instances[] missing")

    # Basic file existence checks.
    for i, fr in enumerate(frames):
        fp = dst / str(fr.get("file_path"))
        if not fp.exists():
            raise FileNotFoundError(f"missing frame {i}: {fp}")

    for inst in instances:
        pidx = int(inst.get("pass_index"))
        mdir = dst / "masks" / f"obj{pidx:02d}"
        if not mdir.exists():
            raise FileNotFoundError(f"missing masks dir: {mdir}")

    if args.normalize_frames:
        # Renaming frames and masks to follow i -> {i:04d}.png.
        rename_map = {}
        for i, fr in enumerate(frames):
            old = dst / str(fr["file_path"])
            new_name = f"{i:04d}.png"
            new = dst / new_name
            if old.name != new_name:
                rename_map[old.name] = new_name
                old.rename(new)
                fr["file_path"] = new_name
        # Masks are already indexed by frame i (0000.png etc) in the exporter; keep as-is.
        (dst / "transforms.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(str(dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

