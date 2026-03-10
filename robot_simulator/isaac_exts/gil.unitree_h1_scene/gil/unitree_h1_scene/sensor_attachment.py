from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import carb
import numpy as np
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema


@dataclass
class AttachedSensorPrims:
    """
    Store *USD prim paths* for sensors, not instantiated Python sensor wrapper objects.

    IMPORTANT:
    Creating `IMUSensor` / `ContactSensor` Python objects can trigger PhysX tensors /
    SimulationView initialization. During startup (or while the USD stage is still being
    rebuilt), that can lead to:
      - "No USD stage attached"
      - "Failed to create simulation view"
      - "simulationView was invalidated"
    and can freeze streaming sessions.
    """

    imu_prim_path: str | None
    contacts: dict[str, str]


def _find_collision_parents(root_prim_path: str = "/World/Humanoid") -> list[str]:
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return []
    root = stage.GetPrimAtPath(root_prim_path)
    if not (root and root.IsValid()):
        return []

    def _pick_link_ancestor(p: Usd.Prim) -> Usd.Prim | None:
        """
        ContactSensor works best when authored under a *link* prim, not a collision mesh prim.
        Many vendor USDs put CollisionAPI on leaf meshes like .../collisions/mesh_0.
        Walk up to the nearest plausible link/root prim to avoid fabric warnings on missing mesh paths.
        """
        try:
            cur = p
            for _ in range(12):
                if not (cur and cur.IsValid()):
                    return None
                if cur.IsInstanceProxy():
                    return None
                name = (cur.GetName() or "").lower()
                if name.endswith("_link") or name in ("pelvis", "torso", "base", "root"):
                    return cur
                if cur.GetPath().pathString == root_prim_path:
                    return cur
                parent = cur.GetParent()
                if not (parent and parent.IsValid()):
                    return cur
                cur = parent
            return cur
        except Exception:
            return None

    out_set: set[str] = set()
    for p in Usd.PrimRange(root):
        try:
            if p.IsInstanceProxy():
                continue
            has_usd = p.HasAPI(UsdPhysics.CollisionAPI)
            has_physx = p.HasAPI(PhysxSchema.PhysxCollisionAPI)
            if not (has_usd or has_physx):
                continue

            link_prim = _pick_link_ancestor(p)
            if link_prim is None:
                continue

            # Some vendor assets only apply PhysX collision API. ContactSensor relies on CollisionAPI being present
            # somewhere sensible; apply it to the selected link prim (best-effort) rather than leaf collision meshes.
            if not link_prim.HasAPI(UsdPhysics.CollisionAPI):
                try:
                    UsdPhysics.CollisionAPI.Apply(link_prim)
                except Exception:
                    pass

            if link_prim.HasAPI(UsdPhysics.CollisionAPI):
                out_set.add(link_prim.GetPath().pathString)
        except Exception:
            continue

    # Fallback: some vendor robots (including Unitree H1-2 packs) keep collision geometry inside instances/proxies
    # which we cannot author to. In that case, create sensors under the link prims themselves by applying a
    # lightweight CollisionAPI to the link prims (best-effort).
    if not out_set:
        link_candidates: list[str] = []
        for p in Usd.PrimRange(root):
            try:
                if p.IsInstanceProxy():
                    continue
                name = p.GetName() or ""
                if not name:
                    continue
                nl = name.lower()
                if not nl.endswith("_link") and nl not in ("pelvis", "torso", "base", "root"):
                    continue
                if any(tok in nl for tok in ("pelvis", "torso", "imu", "ankle", "foot", "wrist", "hand")):
                    try:
                        UsdPhysics.CollisionAPI.Apply(p)
                    except Exception:
                        pass
                    link_candidates.append(p.GetPath().pathString)
            except Exception:
                continue
        out_set = set(link_candidates)
    return sorted(out_set)


def _pick_best_matches(collision_prim_paths: list[str]) -> dict[str, str]:
    """
    Heuristic matching so we can attach sensors to a wide range of Unitree USDs.
    Returns a dict with keys: torso, left_foot, right_foot, left_hand, right_hand.
    """
    def pick(tokens: list[str]) -> str | None:
        toks = [t.lower() for t in tokens]
        for p in collision_prim_paths:
            pl = p.lower()
            if any(t in pl for t in toks):
                return p
        return None

    # Prefer specific link names if present
    torso = pick(["imu_in_torso", "torso", "pelvis", "base"])
    left_foot = pick(["left_foot", "l_foot", "left_ankle", "left_toe"])
    right_foot = pick(["right_foot", "r_foot", "right_ankle", "right_toe"])
    left_hand = pick(["left_hand", "l_hand", "left_wrist", "left_palm"])
    right_hand = pick(["right_hand", "r_hand", "right_wrist", "right_palm"])

    # If hand/foot collisions aren't exposed at link-level, fall back to the first collision under those limbs.
    # (We already searched tokens in the full path.)
    return {
        # IMPORTANT: don't fall back to an arbitrary collision prim for torso; that can pick a knee/etc and
        # produce invalid collision mesh paths (and noisy warnings). Prefer "none" over "wrong".
        "torso": torso or "",
        "left_foot": left_foot or "",
        "right_foot": right_foot or "",
        "left_hand": left_hand or "",
        "right_hand": right_hand or "",
    }


def ensure_full_humanoid_sensors(
    root_prim_path: str = "/World/Humanoid",
    dt: float = 0.02,
) -> AttachedSensorPrims:
    """
    Ensure a small set of physics sensor *PRIMS* exists that approximates Unitree IsaacLab "full":
    - 1 IMU prim on torso/pelvis
    - contact sensor prims on torso + both feet + both hands (where possible)

    NOTE:
    This function intentionally does NOT instantiate Python `IMUSensor`/`ContactSensor` wrappers.
    Those should be created later by the consumer (e.g. after timeline is playing and physics is ready).
    """
    # Global kill-switch: allow disabling all physics sensor prim authoring (helps avoid PhysX startup issues).
    try:
        s = carb.settings.get_settings()
        enable = s.get("/gil/sensors/enable")
        enable = True if enable is None else bool(enable)
        if not enable:
            return AttachedSensorPrims(imu_prim_path=None, contacts={})
    except Exception:
        # If settings aren't available, be conservative: don't author sensors.
        return AttachedSensorPrims(imu_prim_path=None, contacts={})

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return AttachedSensorPrims(imu_prim_path=None, contacts={})

    collision_parents = _find_collision_parents(root_prim_path=root_prim_path)
    if not collision_parents:
        carb.log_warn("[gil.unitree_h1_scene] No collision-enabled prims found; cannot attach contact sensors.")

    picked = _pick_best_matches(collision_parents)

    def _ensure_prim(path: str, type_name: str) -> bool:
        try:
            if not path:
                return False
            p = stage.GetPrimAtPath(path)
            if p and p.IsValid():
                # If prim exists but has no type, try to define it with the desired type.
                if not (p.GetTypeName() or ""):
                    try:
                        stage.DefinePrim(path, type_name)
                    except Exception:
                        pass
                return True
            stage.DefinePrim(path, type_name)
            return True
        except Exception:
            return False

    def _mk_imu(parent: str) -> str | None:
        if not parent:
            return None
        prim_path = f"{parent}/imu_sensor"
        try:
            # Validate parent prim exists before attempting to create a sensor under it.
            pp = stage.GetPrimAtPath(parent)
            if not (pp and pp.IsValid()):
                return None
            if _ensure_prim(prim_path, "IsaacImuSensor"):
                return prim_path
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] Failed to create IMU sensor at {prim_path}: {e!r}")
            return None
        return None

    def _mk_contact(parent: str, name: str) -> str | None:
        if not parent:
            return None
        prim_path = f"{parent}/{name}"
        try:
            pp = stage.GetPrimAtPath(parent)
            if not (pp and pp.IsValid()):
                return None
            if _ensure_prim(prim_path, "IsaacContactSensor"):
                return prim_path
        except Exception as e:
            carb.log_warn(f"[gil.unitree_h1_scene] Failed to create contact sensor at {prim_path}: {e!r}")
            return None
        return None

    imu_prim_path = _mk_imu(picked.get("torso", ""))

    contacts: dict[str, str] = {}
    for key, parent in [
        ("torso", picked.get("torso", "")),
        ("left_foot", picked.get("left_foot", "")),
        ("right_foot", picked.get("right_foot", "")),
        ("left_hand", picked.get("left_hand", "")),
        ("right_hand", picked.get("right_hand", "")),
    ]:
        cs_path = _mk_contact(parent, "contact_sensor")
        if cs_path:
            contacts[key] = cs_path

    carb.log_info(f"[gil.unitree_h1_scene] attached_sensor_prims={{'imu': {imu_prim_path}, 'contacts': {contacts}}}")
    return AttachedSensorPrims(imu_prim_path=imu_prim_path, contacts=contacts)


