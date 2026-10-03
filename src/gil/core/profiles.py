from __future__ import annotations

import json
from pathlib import Path

from gil.core.config import RobotProfile, profile_from_legacy

REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILES_DIR = REPO_ROOT / "profiles"


def load_profile(source: str | Path | dict) -> RobotProfile:
    if isinstance(source, dict):
        return profile_from_legacy(source)
    path = Path(source)
    if not path.is_file():
        candidate = PROFILES_DIR / f"{source}.json"
        if candidate.is_file():
            path = candidate
        else:
            raise FileNotFoundError(f"Robot profile not found: {source}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Profile {path} is not a JSON object")
    return profile_from_legacy(raw)


def list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
