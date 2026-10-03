from __future__ import annotations

from typing import Any

from gil.core.types import Goal
from gil.world.dream import Rollout


class SceneGraphWorldModel:
    """Imagination for tabletop / manipulator scenes.

    Dreams end-effector deltas toward named objects in the observation
    (cubes, bins). Does not issue motors — commands are proposals only.
    """

    def dream(self, observation: dict[str, Any], goal: Goal, n: int) -> list[Rollout]:
        objects = _objects(observation)
        eef = observation.get("end_effector") or observation.get("ee") or {}
        ex = float(eef.get("x", 0.4) or 0.4)
        ey = float(eef.get("y", 0.2) or 0.2)
        ez = float(eef.get("z", 0.3) or 0.3)
        target = _pick_target(objects, goal)
        out: list[Rollout] = []
        for i in range(max(1, n)):
            success = bool(target)
            commands = []
            if target:
                tx, ty, tz = target
                commands = [
                    {
                        "type": "move_robot",
                        "x": tx,
                        "y": ty,
                        "z": tz + 0.12,
                        "from": {"x": ex, "y": ey, "z": ez},
                    },
                    {"type": "gripper", "open": False},
                    {
                        "type": "move_robot",
                        "x": float(goal.x if goal.x is not None else tx + 0.15),
                        "y": float(goal.y if goal.y is not None else ty),
                        "z": tz + 0.18,
                    },
                    {"type": "gripper", "open": True},
                ]
            out.append(
                Rollout(
                    success=success and i < n - 1,
                    critic_score=0.85 if success else 0.2,
                    commands=commands,
                    physics_ok=success,
                    note="scene_graph_pick_place" if success else "scene_graph_no_target",
                )
            )
        return out


def _objects(observation: dict[str, Any]) -> list[dict[str, Any]]:
    raw = observation.get("objects") or observation.get("cubes") or []
    if isinstance(raw, dict):
        return [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in raw.items()]
    return [o for o in raw if isinstance(o, dict)]


def _pick_target(objects: list[dict[str, Any]], goal: Goal) -> tuple[float, float, float] | None:
    lang = (goal.language or "").lower()
    named = None
    for o in objects:
        name = str(o.get("name") or o.get("id") or "").lower()
        color = str(o.get("color") or "").lower()
        if color and color in lang:
            named = o
            break
        if name and name in lang:
            named = o
            break
    if named is None and objects:
        named = objects[0]
    if named is None:
        if goal.x is None:
            return None
        return (float(goal.x), float(goal.y or 0.0), 0.12)
    pos = named.get("position") or named.get("pose") or named
    try:
        return (float(pos.get("x", 0.5)), float(pos.get("y", 0.0)), float(pos.get("z", 0.12)))
    except (TypeError, ValueError, AttributeError):
        return None
