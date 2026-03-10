import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Transition:
    episode: int
    t: int
    seed: int
    stage: int
    action_delta_xyz: List[float]
    action_gripper_open_cmd: Optional[bool]
    state: Dict[str, Any]
    done: bool
    success: bool
    image_left_path: Optional[str]
    target_cube: Optional[str]


def _safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def load_transitions(run_dir: str) -> List[Transition]:
    run_dir = os.path.abspath(run_dir)
    path = os.path.join(run_dir, "transitions.jsonl")
    if not os.path.exists(path):
        raise FileNotFoundError(f"transitions.jsonl not found: {path}")

    out: List[Transition] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            action_delta_xyz = _safe_get(row, ["action", "delta_xyz"], default=[0.0, 0.0, 0.0])
            gr = _safe_get(row, ["action", "gripper_open_cmd"], default=None)
            img_left = _safe_get(row, ["images", "paths", "image_left"], default=None)
            target_cube = _safe_get(row, ["state", "target_cube"], default=None)
            out.append(
                Transition(
                    episode=int(row.get("episode", 0)),
                    t=int(row.get("t", 0)),
                    seed=int(row.get("seed", 0)),
                    stage=int(row.get("stage", 0)),
                    action_delta_xyz=[float(x) for x in action_delta_xyz],
                    action_gripper_open_cmd=None if gr is None else bool(gr),
                    state=row.get("state", {}) if isinstance(row.get("state"), dict) else {},
                    done=bool(row.get("done", False)),
                    success=bool(row.get("success", False)),
                    image_left_path=img_left if isinstance(img_left, str) else None,
                    target_cube=target_cube if isinstance(target_cube, str) else None,
                )
            )
    return out


def split_by_episode(transitions: List[Transition]) -> Dict[int, List[Transition]]:
    eps: Dict[int, List[Transition]] = {}
    for tr in transitions:
        eps.setdefault(tr.episode, []).append(tr)
    for k in eps:
        eps[k].sort(key=lambda x: x.t)
    return eps


def make_state_vector(state: Dict[str, Any]) -> List[float]:
    # Minimal vector for baseline: ee, cube, bin, gripper_open, held
    ee = state.get("ee_xyz") or [0.0, 0.0, 0.0]
    cube = state.get("cube_xyz") or [0.0, 0.0, 0.0]
    bin_xyz = state.get("bin_xyz") or [0.0, 0.0, 0.0]
    gripper_open = state.get("gripper_open") if state.get("gripper_open") is not None else 1.0
    held = state.get("held") if state.get("held") is not None else 0.0

    # If present, use target_cube + cubes[] list to derive cube xyz/held for the selected target.
    target = state.get("target_cube")
    cubes = state.get("cubes")
    if isinstance(target, str) and isinstance(cubes, list):
        for c in cubes:
            if isinstance(c, dict) and c.get("name") == target:
                cube = [c.get("x", 0.0), c.get("y", 0.0), c.get("z", 0.0)]
                held = 1.0 if bool(c.get("held", False)) else 0.0
                break

    vec = [float(x) for x in ee] + [float(x) for x in cube] + [float(x) for x in bin_xyz] + [float(gripper_open), float(held)]
    return vec


