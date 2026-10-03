from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from gil.core.config import RobotProfile


@dataclass
class DetectedObject:
    label: str
    x: float
    y: float
    z: float = 0.0
    occupied: bool = True


@dataclass
class MapSnapshot:
    calibrated: bool
    coverage: float
    objects: list[DetectedObject]
    occupied: set[tuple[int, int]]
    path_ok: bool = True

    def summary(self) -> dict[str, Any]:
        return {
            "calibrated": self.calibrated,
            "coverage": self.coverage,
            "object_count": len(self.objects),
            "occupied_cells": len(self.occupied),
            "safe_for_motion_authority": self.calibrated,
        }


class SceneMap:
    """Occupancy + object index. Uncalibrated cameras cannot authorize motion."""

    def __init__(self, profile: RobotProfile | None = None, cell_m: float = 0.25):
        self.profile = profile
        self.cell_m = cell_m
        self.objects: list[DetectedObject] = []
        self.occupied: set[tuple[int, int]] = set()
        self._cells_seen: set[tuple[int, int]] = set()
        self._extent = 16

    @property
    def calibrated(self) -> bool:
        if self.profile is None:
            return False
        return self.profile.cameras_calibrated

    def ingest(self, observation: dict[str, Any]) -> MapSnapshot:
        objects = list(_objects_from_observation(observation))
        self.objects = objects
        for obj in objects:
            cell = self._cell(obj.x, obj.y)
            self._cells_seen.add(cell)
            if obj.occupied:
                self.occupied.add(cell)
        for item in observation.get("obstacles") or []:
            if isinstance(item, dict):
                ox, oy = float(item.get("x", 0.0)), float(item.get("y", 0.0))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                ox, oy = float(item[0]), float(item[1])
            else:
                continue
            cell = self._cell(ox, oy)
            self.occupied.add(cell)
            self._cells_seen.add(cell)
        pose = observation.get("base") or observation.get("pose") or {}
        if "x" in pose and "y" in pose:
            self._cells_seen.add(self._cell(float(pose["x"]), float(pose["y"])))
        coverage = min(1.0, len(self._cells_seen) / float(self._extent * self._extent))
        if objects or observation.get("obstacles"):
            coverage = max(coverage, 0.55 if self.calibrated else 0.2)
        return MapSnapshot(
            calibrated=self.calibrated,
            coverage=coverage,
            objects=list(self.objects),
            occupied=set(self.occupied),
        )

    def locate(self, label: str) -> DetectedObject | None:
        needle = label.strip().lower()
        for obj in self.objects:
            if needle in obj.label.lower():
                return obj
        return None

    def occupancy_at(self, x: float, y: float) -> bool:
        return self._cell(x, y) in self.occupied

    def shortest_path(self, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[int, int]] | None:
        s = self._cell(*start)
        g = self._cell(*goal)
        if s == g:
            return [s]
        frontier = [s]
        came = {s: None}
        while frontier:
            cur = frontier.pop(0)
            if cur == g:
                break
            cx, cy = cur
            for nxt in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if nxt in came or nxt in self.occupied:
                    continue
                if abs(nxt[0]) > self._extent or abs(nxt[1]) > self._extent:
                    continue
                came[nxt] = cur
                frontier.append(nxt)
        if g not in came:
            return None
        path = [g]
        while came[path[-1]] is not None:
            path.append(came[path[-1]])  # type: ignore[arg-type]
        path.reverse()
        return path

    def summary(self) -> dict[str, Any]:
        return MapSnapshot(
            calibrated=self.calibrated,
            coverage=min(1.0, len(self._cells_seen) / float(self._extent * self._extent)) if self._cells_seen else 0.0,
            objects=list(self.objects),
            occupied=set(self.occupied),
        ).summary()

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return int(round(x / self.cell_m)), int(round(y / self.cell_m))


def _objects_from_observation(observation: dict[str, Any]) -> Iterable[DetectedObject]:
    raw = observation.get("objects") or observation.get("scene_objects") or []
    if isinstance(raw, dict):
        for label, pose in raw.items():
            if isinstance(pose, dict):
                yield DetectedObject(label=str(label), x=float(pose.get("x", 0)), y=float(pose.get("y", 0)), z=float(pose.get("z", 0)))
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        yield DetectedObject(
            label=str(item.get("label") or item.get("name") or "object"),
            x=float(item.get("x", 0.0)),
            y=float(item.get("y", 0.0)),
            z=float(item.get("z", 0.0)),
            occupied=bool(item.get("occupied", True)),
        )
