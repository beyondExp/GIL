from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CARDS_DIR = Path(__file__).resolve().parent
REQUIRED_FIELDS = ("protocol", "name", "url", "skills")


def load_card(name: str) -> dict[str, Any]:
    path = CARDS_DIR / f"{name}.card.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_card(data)
    return data


def list_cards() -> list[str]:
    return sorted(p.name.replace(".card.json", "") for p in CARDS_DIR.glob("*.card.json"))


def validate_card(card: dict[str, Any]) -> None:
    for field in REQUIRED_FIELDS:
        if field not in card:
            raise ValueError(f"A2A card missing '{field}'")
    if card.get("protocol") != "a2a":
        raise ValueError("A2A card protocol must be 'a2a'")
    if not isinstance(card.get("skills"), list) or not card["skills"]:
        raise ValueError("A2A card must list skills")
