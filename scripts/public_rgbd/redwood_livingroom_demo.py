#!/usr/bin/env python3
"""
One-command public RGB-D demo:

  - downloads a public RGB-D scene (Redwood LivingRoom) via Open3D
  - fuses RGB-D into a TSDF volume
  - extracts a mesh + pointcloud overlay
  - writes outputs under assets/public_rgbd/redwood_livingroom_v1/
  - launches Isaac Sim with maze disabled and the reconstructed env loaded

The goal is a "great demo" pipeline even without your own capture.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "assets" / "public_rgbd" / "redwood_livingroom_v1"


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"[run] {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def _ensure_open3d(auto_install: bool = True) -> None:
    try:
        import open3d as _o3d  # noqa: F401

        return
    except Exception as e:
        if not auto_install:
            raise
        print(f"[deps] Open3D missing ({e!r}); installing open3d==0.19.0...", flush=True)
        _run([sys.executable, "-m", "pip", "install", "open3d==0.19.0"])


def _read_redwood_traj_txt(path: Path) -> list[list[list[float]]]:
    """
    Parse Redwood trajectory txt (blocks of 1 header line + 4 matrix lines).

    Observed format:
      i i (i+1)
      r00 r01 r02 t0
      r10 r11 r12 t1
      r20 r21 r22 t2
      0 0 0 1

    We treat the 4x4 as camera-to-world for frame i.
    """
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out: dict[int, list[list[float]]] = {}
    k = 0
    while k + 4 < len(lines):
        head = lines[k].split()
        if len(head) >= 1 and all(p.replace("-", "").isdigit() for p in head[:1]):
            try:
                i = int(head[0])
            except Exception:
                i = None
            if i is not None:
                try:
                    m = []
                    for r in range(4):
                        m.append([float(x) for x in lines[k + 1 + r].split()])
                    if len(m) == 4 and all(len(rr) == 4 for rr in m):
                        out[i] = m
                        k += 5
                        continue
                except Exception:
                    pass
        k += 1
    # Return in order (dense index expected)
    if not out:
        return []
    mx = max(out.keys())
    return [out[i] for i in range(mx + 1) if i in out]

def _decide_extrinsic_convention(
    *,
    volume,
    intrinsic,
    color_files: list[Path],
    depth_files: list[Path],
    extrinsics_c2w_guess: list[list[list[float]]],
    gt_ply: Path,
    test_frames: int = 150,
) -> bool:
    """
    Redwood traj files are ambiguous (some toolchains store c2w, others w2c).
    Decide whether to invert extrinsics by comparing a quick TSDF pointcloud to the dataset's GT pointcloud.

    Returns True if we should invert the provided matrix (i.e., treat file as c2w and Open3D wants w2c), else False.
    """
    import numpy as np
    import open3d as o3d

    if not gt_ply.exists():
        return False
    try:
        gt = o3d.io.read_point_cloud(str(gt_ply))
        gt = gt.voxel_down_sample(voxel_size=0.05)
    except Exception:
        return False

    def _score(use_inv: bool) -> float:
        vol = volume  # cloned outside (caller passes a fresh volume)
        # Integrate a small subset
        n = min(test_frames, len(extrinsics_c2w_guess), len(color_files), len(depth_files))
        for i in range(n):
            color = o3d.io.read_image(str(color_files[i]))
            depth = o3d.io.read_image(str(depth_files[i]))
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                color,
                depth,
                depth_scale=1000.0,
                depth_trunc=4.0,
                convert_rgb_to_intensity=False,
            )
            T = np.asarray(extrinsics_c2w_guess[i], dtype=np.float64)
            extr = np.linalg.inv(T) if use_inv else T
            vol.integrate(rgbd, intrinsic, extr)
        src = vol.extract_point_cloud()
        src = src.voxel_down_sample(voxel_size=0.05)
        if len(src.points) < 2000:
            return 0.0
        # ICP with identity init (same metric frame expected)
        reg = o3d.pipelines.registration.registration_icp(
            src,
            gt,
            0.30,
            np.eye(4),
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
        )
        # Higher fitness is better (inliers fraction)
        return float(reg.fitness)

    # (Unused helper retained for future refactors.)
    return False


def _tsdf_reconstruct(
    *,
    max_frames: int = 500,
    voxel_size_m: float = 0.03,
    trunc_m: float = 0.10,
    max_faces: int = 1_000_000,
    overlay_points: int = 200_000,
    seed: int = 42,
) -> dict[str, Path]:
    import numpy as np
    import open3d as o3d

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "previews").mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "recon").mkdir(parents=True, exist_ok=True)

    # Download dataset
    print("[data] downloading RedwoodIndoorLivingRoom...", flush=True)
    # Open3D names differ across versions; prefer LivingRoom1.
    ds = None
    for cls_name in ("RedwoodIndoorLivingRoom1", "RedwoodIndoorLivingRoom2"):
        try:
            cls = getattr(o3d.data, cls_name)
            ds = cls()
            break
        except Exception:
            ds = None
    if ds is None:
        raise RuntimeError("Open3D Redwood Indoor LivingRoom dataset class not found in this Open3D build.")
    color_files = [Path(p) for p in getattr(ds, "color_paths", [])]
    depth_files = [Path(p) for p in getattr(ds, "depth_paths", [])]
    traj_path = Path(getattr(ds, "trajectory_path", ""))
    if (not color_files) or (not depth_files) or (not traj_path.exists()):
        raise RuntimeError("Open3D dataset files missing after download.")

    # Redwood trajectory txt -> 4x4 matrices (convention varies by source; we will auto-detect).
    extrinsics = _read_redwood_traj_txt(traj_path)
    if not extrinsics:
        raise RuntimeError("Failed to parse Redwood trajectory file (no poses).")

    # Intrinsics: Redwood sequences are PrimeSense-like (640x480).
    intrinsic = o3d.camera.PinholeCameraIntrinsic(o3d.camera.PinholeCameraIntrinsicParameters.PrimeSenseDefault)

    # Figure frames
    # The dataset already provides ordered per-frame paths.
    # (Keep as-is; do not re-sort unless needed.)
    color_files = list(color_files)
    depth_files = list(depth_files)

    total = min(len(extrinsics), len(color_files), len(depth_files))
    if total <= 0:
        raise RuntimeError("No frames found to reconstruct.")

    want = int(max_frames)
    want = total if want <= 0 else min(want, total)

    # Important: Redwood sequences are long (~2870 frames). Using only the first N frames can
    # easily reconstruct "just a wall" depending on where the camera starts. We therefore
    # sample frames evenly across the whole sequence by default.
    if want < total:
        idx = np.linspace(0, total - 1, num=want, dtype=int)
        # Ensure strictly increasing indices (linspace can repeat for small want/large total)
        idx = np.unique(idx)
        if idx.size < want:
            # Pad deterministically with additional indices if uniq shrank
            extra = np.setdiff1d(np.arange(total, dtype=int), idx, assume_unique=False)
            take = min(int(want - idx.size), int(extra.size))
            idx = np.sort(np.concatenate([idx, extra[:take]]))
        frame_idx = idx.tolist()
    else:
        frame_idx = list(range(total))

    # Apply selection
    extrinsics = [extrinsics[i] for i in frame_idx]
    color_files = [color_files[i] for i in frame_idx]
    depth_files = [depth_files[i] for i in frame_idx]
    n = len(frame_idx)

    print(f"[data] frames={n}/{total} voxel={voxel_size_m} trunc={trunc_m} sampling=even", flush=True)

    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=float(voxel_size_m),
        sdf_trunc=float(trunc_m),
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )

    # Decide whether to invert the trajectory matrices.
    # We compare a small TSDF pointcloud to the dataset's shipped GT pointcloud livingroom.ply.
    use_inv = False
    try:
        # Build two tiny volumes for scoring (so we don't pollute the real volume).
        v0 = o3d.pipelines.integration.ScalableTSDFVolume(
            voxel_length=max(0.05, float(voxel_size_m) * 2.0),
            sdf_trunc=max(0.15, float(trunc_m)),
            color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
        )
        v1 = o3d.pipelines.integration.ScalableTSDFVolume(
            voxel_length=max(0.05, float(voxel_size_m) * 2.0),
            sdf_trunc=max(0.15, float(trunc_m)),
            color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
        )
        gt_ply = Path(getattr(ds, "point_cloud_path", "")) if hasattr(ds, "point_cloud_path") else Path("")
        # Inline scoring (avoid extra deps): integrate 150 frames and compute ICP fitness.
        def _score(vol, inv: bool) -> float:
            ntest = min(150, n)
            for i in range(ntest):
                color = o3d.io.read_image(str(color_files[i]))
                depth = o3d.io.read_image(str(depth_files[i]))
                rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                    color, depth, depth_scale=1000.0, depth_trunc=4.0, convert_rgb_to_intensity=False
                )
                T = np.asarray(extrinsics[i], dtype=np.float64)
                extr = np.linalg.inv(T) if inv else T
                vol.integrate(rgbd, intrinsic, extr)
            src = vol.extract_point_cloud().voxel_down_sample(voxel_size=0.05)
            if not gt_ply.exists():
                return 0.0
            tgt = o3d.io.read_point_cloud(str(gt_ply)).voxel_down_sample(voxel_size=0.05)
            if len(src.points) < 2000 or len(tgt.points) < 2000:
                return 0.0
            reg = o3d.pipelines.registration.registration_icp(
                src, tgt, 0.30, np.eye(4), o3d.pipelines.registration.TransformationEstimationPointToPoint()
            )
            return float(reg.fitness)

        f_no = _score(v0, False)
        f_inv = _score(v1, True)
        use_inv = bool(f_inv > (f_no + 0.05))
        print(f"[traj] icp_fitness no_inv={f_no:.3f} inv={f_inv:.3f} -> use_inv={use_inv}", flush=True)
    except Exception as e:
        print(f"[traj] auto-detect skipped: {e!r} (default use_inv={use_inv})", flush=True)

    t0 = time.time()
    for i in range(n):
        cpath = color_files[i]
        dpath = depth_files[i]
        color = o3d.io.read_image(str(cpath))
        depth = o3d.io.read_image(str(dpath))
        # Redwood depth is in mm; Open3D uses default depth_scale=1000.0 for mm.
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            color,
            depth,
            depth_scale=1000.0,
            depth_trunc=4.0,
            convert_rgb_to_intensity=False,
        )
        T = np.asarray(extrinsics[i], dtype=np.float64)
        extr = np.linalg.inv(T) if use_inv else T
        volume.integrate(rgbd, intrinsic, extr)
        if (i + 1) % 50 == 0 or i == n - 1:
            dt = time.time() - t0
            print(f"[tsdf] integrated {i+1}/{n} ({dt:.1f}s)", flush=True)

    # Extract mesh
    mesh = volume.extract_triangle_mesh()
    mesh.compute_vertex_normals()

    def _orient_mesh_by_floor_plane(m: "o3d.geometry.TriangleMesh") -> "o3d.geometry.TriangleMesh":
        """
        Make the reconstruction right-side-up by estimating the dominant floor plane and
        rotating so its normal aligns with +Z (Isaac is Z-up).
        """
        try:
            # Sample points (use a lot; plane fit is robust and fast enough here)
            p = m.sample_points_uniformly(number_of_points=250_000)
            if len(p.points) < 10_000:
                return m

            pts = np.asarray(p.points, dtype=np.float64)
            # Heuristic: floor is among the lowest points. Restrict plane fitting to the lowest quantile
            # to avoid picking a big wall plane.
            z = pts[:, 2]
            z_cut = float(np.quantile(z, 0.25))
            low_idx = np.where(z <= z_cut)[0]
            if low_idx.size < 20_000:
                low_idx = np.argsort(z)[: min(int(z.size), 60_000)]
            p_low = o3d.geometry.PointCloud()
            p_low.points = o3d.utility.Vector3dVector(pts[low_idx])

            # RANSAC plane on low points (floor candidate)
            plane_model, inliers = p_low.segment_plane(distance_threshold=0.03, ransac_n=3, num_iterations=2500)
            if not inliers or len(inliers) < 10_000:
                return m
            a, b, c, d = [float(x) for x in plane_model]
            n = np.asarray([a, b, c], dtype=np.float64)
            nn = float(np.linalg.norm(n))
            if nn < 1e-8:
                return m
            n = n / nn

            z = np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
            # If already aligned (up to sign), just flip if upside down.
            dot = float(np.clip(np.dot(n, z), -1.0, 1.0))
            if dot < -0.2:
                # Upside down: rotate 180° about Y (keep X, flip Z)
                R = np.array([[-1.0, 0.0, 0.0],
                              [0.0, 1.0, 0.0],
                              [0.0, 0.0, -1.0]], dtype=np.float64)
            elif dot > 0.98:
                R = np.eye(3, dtype=np.float64)
            else:
                # General case: rotate n -> +Z
                v = np.cross(n, z)
                s = float(np.linalg.norm(v))
                if s < 1e-8:
                    R = np.eye(3, dtype=np.float64)
                else:
                    v = v / s
                    vx, vy, vz = v.tolist()
                    K = np.array([[0.0, -vz, vy],
                                  [vz, 0.0, -vx],
                                  [-vy, vx, 0.0]], dtype=np.float64)
                    # Rodrigues
                    R = np.eye(3, dtype=np.float64) + K * s + (K @ K) * ((1.0 - dot))

            # Apply rotation about mesh center to avoid drifting far away
            V = np.asarray(m.vertices, dtype=np.float64)
            if V.size == 0:
                return m
            c0 = V.mean(axis=0)
            V = (V - c0) @ R.T + c0
            m.vertices = o3d.utility.Vector3dVector(V)
            m.compute_vertex_normals()
            return m
        except Exception:
            return m

    # Align to dataset GT pointcloud to fix any global frame flips/rotations.
    # This helps ensure Isaac shows a correctly oriented room even if a traj convention differs.
    try:
        gt_ply = Path(getattr(ds, "point_cloud_path", ""))
        if gt_ply.exists():
            tgt = o3d.io.read_point_cloud(str(gt_ply)).voxel_down_sample(voxel_size=0.05)
            src = mesh.sample_points_uniformly(number_of_points=80_000).voxel_down_sample(voxel_size=0.05)
            if len(src.points) > 2000 and len(tgt.points) > 2000:
                reg = o3d.pipelines.registration.registration_icp(
                    src,
                    tgt,
                    0.50,
                    np.eye(4),
                    o3d.pipelines.registration.TransformationEstimationPointToPoint(),
                )
                T_align = reg.transformation
                mesh.transform(T_align)
                print(f"[align] icp_fitness={float(reg.fitness):.3f} rmse={float(reg.inlier_rmse):.3f}", flush=True)
    except Exception as e:
        print(f"[align] skipped: {e!r}", flush=True)

    # Ensure "up" is correct (fix upside-down / tilted rooms).
    mesh = _orient_mesh_by_floor_plane(mesh)

    # Center + floor align (demo-friendly)
    v = np.asarray(mesh.vertices)
    if v.size:
        center_xy = v[:, :2].mean(axis=0)
        min_z = float(v[:, 2].min())
        v[:, 0] -= float(center_xy[0])
        v[:, 1] -= float(center_xy[1])
        v[:, 2] -= min_z
        mesh.vertices = o3d.utility.Vector3dVector(v)

    # Simplify (target triangles)
    try:
        tri_n = int(len(mesh.triangles))
        if tri_n > max_faces and max_faces > 0:
            mesh = mesh.simplify_quadric_decimation(int(max_faces))
            mesh.compute_vertex_normals()
    except Exception:
        pass

    mesh_ply = OUT_ROOT / "recon" / "mesh.ply"
    o3d.io.write_triangle_mesh(str(mesh_ply), mesh, write_ascii=False, compressed=True)

    # Extract pointcloud overlay (prefer sampling the mesh, which is typically much denser + better colored)
    pts = np.zeros((0, 3), dtype=np.float32)
    cols = np.zeros((0, 3), dtype=np.float32)
    try:
        if overlay_points and int(overlay_points) > 0:
            # poisson disk is higher quality; can fail for some meshes.
            try:
                pcd = mesh.sample_points_poisson_disk(number_of_points=int(overlay_points), init_factor=5)
            except Exception:
                pcd = mesh.sample_points_uniformly(number_of_points=int(overlay_points))
            pts = np.asarray(pcd.points, dtype=np.float32)
            cols = np.asarray(getattr(pcd, "colors", np.zeros((pts.shape[0], 3), dtype=np.float32)), dtype=np.float32)
    except Exception:
        pts = np.zeros((0, 3), dtype=np.float32)
        cols = np.zeros((0, 3), dtype=np.float32)

    if pts.size == 0:
        # Fallback: pointcloud extracted from TSDF voxels (often sparse)
        pcd = volume.extract_point_cloud()
        pts = np.asarray(pcd.points, dtype=np.float32)
        cols = np.asarray(getattr(pcd, "colors", np.zeros((pts.shape[0], 3), dtype=np.float32)), dtype=np.float32)

    if pts.shape[0] > 0:
        # Apply the same alignment to pcd
        pts[:, 0] -= float(pts[:, 0].mean())
        pts[:, 1] -= float(pts[:, 1].mean())
        pts[:, 2] -= float(pts[:, 2].min())

    cols_u8 = (np.clip(cols, 0.0, 1.0) * 255.0).astype("uint8") if cols.size else np.zeros((pts.shape[0], 3), dtype="uint8")
    pc_npz = OUT_ROOT / "recon" / "overlay_pointcloud.npz"
    np.savez_compressed(pc_npz, points_xyz=pts.astype("float32"), colors_rgb=cols_u8)

    # Note: Open3D OffscreenRenderer is not reliably supported on Windows in all environments
    # (EGL headless). We skip preview rendering and rely on Isaac screenshots for visuals.

    # Write a small manifest for the loader
    (OUT_ROOT / "recon" / "manifest.json").write_text(
        (
            "{\n"
            f"  \"mesh_ply\": \"{mesh_ply.as_posix()}\",\n"
            f"  \"overlay_npz\": \"{pc_npz.as_posix()}\",\n"
            f"  \"frames_used\": {n},\n"
            f"  \"voxel_size_m\": {voxel_size_m},\n"
            f"  \"trunc_m\": {trunc_m}\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    print(f"[ok] mesh={mesh_ply} overlay={pc_npz}", flush=True)
    return {"mesh_ply": mesh_ply, "overlay_npz": pc_npz}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--frames", type=int, default=500)
    p.add_argument("--voxel", type=float, default=0.03)
    p.add_argument("--trunc", type=float, default=0.10)
    p.add_argument("--faces", type=int, default=1_000_000)
    p.add_argument("--overlay_points", type=int, default=200_000)
    p.add_argument("--force", action="store_true", default=False, help="Recompute even if outputs exist.")
    p.add_argument("--no_auto_install", action="store_true", default=False)
    p.add_argument("--no_launch_isaac", action="store_true", default=False, help="Only reconstruct; don't launch Isaac.")
    p.add_argument("--screenshot_after_s", type=float, default=180.0, help="Seconds to wait after launching Isaac before taking a screenshot.")
    args = p.parse_args()

    _ensure_open3d(auto_install=not bool(args.no_auto_install))

    mesh_ply = OUT_ROOT / "recon" / "mesh.ply"
    overlay_npz = OUT_ROOT / "recon" / "overlay_pointcloud.npz"
    if (not args.force) and mesh_ply.exists() and overlay_npz.exists():
        print("[cache] using existing reconstruction outputs", flush=True)
        out = {"mesh_ply": mesh_ply, "overlay_npz": overlay_npz}
    else:
        out = _tsdf_reconstruct(
            max_frames=int(args.frames),
            voxel_size_m=float(args.voxel),
            trunc_m=float(args.trunc),
            max_faces=int(args.faces),
            overlay_points=int(args.overlay_points),
        )

    if bool(args.no_launch_isaac):
        return 0

    # Launch Isaac (leave it running for inspection):
    # - maze disabled
    # - env points to the PLY (Isaac extension auto-converts to USD and caches)
    # - overlay pointcloud enabled for debug
    ps1 = REPO / "scripts" / "run_isaac_h1_maze_real.ps1"
    if not ps1.exists():
        raise FileNotFoundError(str(ps1))

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
        "-Kit",
        "base",
        "-MazeEnabled",
        "0",
        "-EnvUsd",
        str(out["mesh_ply"]),
        "-EnvConverted",
        "-EnvConvertedPointcloudNpz",
        str(out["overlay_npz"]),
        "-EnvConvertedPointSize",
        "0.03",
        # Axis fix: Isaac's mesh asset converter can apply an implicit up-axis conversion
        # that pointcloud overlays don't get. Rotate the overlay to match typical PLY->USD conversion.
        # Let Isaac auto-align the pointcloud overlay to the converted mesh.
        "-EnvConvertedAutoAlign",
        "-AutoPlay",
    ]
    print("[isaac] launching (non-blocking)...", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(REPO))
    print(f"[isaac] pid={proc.pid}", flush=True)

    # Best-effort: wait for it to load + play, then take a screenshot.
    wait_s = float(args.screenshot_after_s)
    print(f"[isaac] waiting {wait_s:.0f}s then taking screenshot...", flush=True)
    time.sleep(max(1.0, wait_s))
    # Bring Isaac window to front (best-effort) so screenshot captures it.
    try:
        subprocess.check_output(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / "scripts" / "focus_isaac.ps1")],
            cwd=str(REPO),
            text=True,
        )
    except Exception:
        pass
    try:
        outp = subprocess.check_output(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / "scripts" / "take_screenshot.ps1")],
            cwd=str(REPO),
            text=True,
        ).strip()
        print(f"[ok] screenshot={outp}", flush=True)
    except Exception as e:
        print(f"[warn] screenshot failed: {e!r}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

