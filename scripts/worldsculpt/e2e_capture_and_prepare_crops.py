#!/usr/bin/env python3
"""
End-to-end smoke test for the Isaac->WorldSculpt GT pipeline.

Pipeline:
  1) Launch Isaac Sim with GIL maze + humanoid and enable /gil/gt_scene export.
  2) Wait for the exporter to write <scene_dir>/_DONE.txt.
  3) Clone WorldSculpt (if missing) into `_third_party/WorldSculpt`.
  4) Run WorldSculpt's prepare_crops_scene.py to validate transforms.json + masks layout.

Notes:
  - This script does NOT run the full WorldSculpt reconstruction/inference (heavy GPU + checkpoints).
    It validates the GT scene contract and produces per-object crops under <case_root>/_crops.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"[run] {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--kit", default="full", choices=["base", "full"])
    p.add_argument("--variant", default="h1", choices=["h1", "h1_2", "g1"])
    p.add_argument("--maze-seed", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=40)
    p.add_argument("--hz", type=float, default=2.0)
    p.add_argument("--max-instances", type=int, default=24)
    p.add_argument("--min-pixels", type=int, default=250)
    p.add_argument("--timeout-s", type=float, default=600.0)
    args = p.parse_args()

    root = repo_root()
    scripts = root / "scripts"

    out_root = root / "assets" / "worldsculpt" / "input"
    out_root.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("isaac_gt_%Y%m%d_%H%M%S")
    scene_dir = out_root / run_id

    # 1) Launch Isaac with GT export enabled. Use CameraCapture so Replicator is on.
    ps1 = scripts / "run_isaac_h1_maze_real.ps1"
    if not ps1.exists():
        raise FileNotFoundError(str(ps1))

    # NOTE: we do not block on Isaac. We wait for _DONE.txt.
    isaac_cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
        "-Kit",
        args.kit,
        "-WalkerMode",
        "external",
        "-HumanoidVariant",
        args.variant,
        "-MazeSeed",
        str(args.maze_seed),
        "-CameraCapture",
        "-GtScene",
        "-GtOutDir",
        str(out_root),
        "-GtRunId",
        run_id,
        "-GtMaxFrames",
        str(args.max_frames),
        "-GtHz",
        str(args.hz),
        "-GtMaxInstances",
        str(args.max_instances),
        "-GtMinPixels",
        str(args.min_pixels),
    ]

    print(f"[isaac] exporting to: {scene_dir}", flush=True)
    proc = subprocess.Popen(isaac_cmd, cwd=str(root))

    # 2) Wait for exporter completion.
    done = scene_dir / "_DONE.txt"
    t0 = time.time()
    while True:
        if done.exists():
            print(f"[ok] GT export done: {done}", flush=True)
            break
        if proc.poll() is not None:
            raise RuntimeError(f"Isaac exited early with code {proc.returncode}; _DONE.txt not found at {done}")
        if (time.time() - t0) > float(args.timeout_s):
            raise TimeoutError(f"Timed out waiting for {done} (timeout={args.timeout_s}s)")
        time.sleep(2.0)

    # Best-effort stop Isaac so subsequent runs are clean.
    try:
        proc.terminate()
    except Exception:
        pass

    # 2b) Convert raw capture -> WorldSculpt GT scene layout (PNG + masks + transforms.json).
    scene_ws = root / "assets" / "worldsculpt" / "scene" / run_id
    scene_ws.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            str(scripts / "worldsculpt" / "build_worldsculpt_scene_from_gil_raw.py"),
            "--raw-scene-dir",
            str(scene_dir),
            "--out-scene-dir",
            str(scene_ws),
        ],
        cwd=root,
    )

    # 3) Clone WorldSculpt if needed (clone under `third_party/` so it isn't ignored by tooling on this repo).
    ws_root = root / "third_party" / "WorldSculpt"
    if not (ws_root / "prepare_crops_scene.py").exists():
        ws_root.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "https://github.com/AlayaLab/WorldSculpt.git", str(ws_root)], cwd=root)

    # 3b) Windows: WorldSculpt's crop prep uses symlinks; on non-admin Windows this can fail.
    # Patch `prepare_crops_scene.py` in-place to fall back to file copy when symlink creation fails.
    try:
        pcs = ws_root / "prepare_crops_scene.py"
        txt = pcs.read_text(encoding="utf-8")
        if "shutil.copy2(" not in txt:
            if "import shutil" not in txt:
                # Insert next to the other imports.
                txt = txt.replace("import os\n", "import os\nimport shutil\n", 1)
            # Replace the last os.symlink(...) in _symlink with a try/copy fallback.
            needle = " os.symlink(rel, dst)\n"
            repl = "    try:\n        os.symlink(rel, dst)\n    except OSError:\n        shutil.copy2(src, dst)\n"
            if needle in txt:
                txt = txt.replace(needle, repl, 1)
                pcs.write_text(txt, encoding="utf-8")
                print(f"[worldsculpt] patched symlink->copy fallback: {pcs}", flush=True)
    except Exception as e:
        print(f"[worldsculpt] WARN: could not patch symlink fallback: {e!r}", flush=True)

    # 4) Run crop stage as a contract validator.
    case_root = root / "assets" / "worldsculpt" / "case" / run_id
    case_root.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            str(ws_root / "prepare_crops_scene.py"),
            "--scene_dir",
            str(scene_ws),
            "--case_root",
            str(case_root),
            "--crop_resolution",
            "512",
            "--save_alignments",
        ],
        cwd=ws_root,
    )

    print(f"[done] crops written under: {case_root / '_crops'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

