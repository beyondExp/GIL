from __future__ import annotations

import re
from pathlib import Path

import pytest

import gil

pytestmark = pytest.mark.phase0

REPO = Path(__file__).resolve().parents[1]
SECRET_RE = re.compile(r"hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")
PRODUCT_PATHS = [
    REPO / "src" / "gil",
    REPO / "profiles",
    REPO / "gil_controls" / "src",
    REPO / "gil_models" / "src",
    REPO / "tests",
    REPO / ".env.example",
    REPO / "README.md",
    REPO / "pyproject.toml",
    REPO / "docs",
]


def test_package_imports_and_version():
    assert gil.__version__ == "0.1.0"
    assert "Intelligence" in gil.PRODUCT


def test_pyproject_and_env_example_exist():
    assert (REPO / "pyproject.toml").is_file()
    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "GIL_HUMANOID_BACKEND" in env
    assert "ISAACSIM_PATH" in env
    assert not SECRET_RE.search(env)


def test_readme_describes_live_product_not_huggingface_hub():
    text = (REPO / "README.md").read_text(encoding="utf-8").lower()
    assert "gil_controls" in text
    assert "orchestrator" in text
    assert "50+" not in text
    assert "huggingface model hub" not in text


def test_product_code_has_no_live_secrets():
    hits = []
    for root in PRODUCT_PATHS:
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".example", ".txt"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for match in SECRET_RE.findall(text):
                body = match.split("_", 1)[-1]
                if "REPLACE" in match or set(body.lower()) <= {"x"}:
                    continue
                hits.append(f"{path}: {match[:12]}...")
    assert hits == []


def test_legacy_mcp_guide_token_is_placeholder():
    guide = REPO / "docs" / "guides" / "MCP_SERVER_GUIDE.md"
    if not guide.is_file():
        pytest.skip("legacy guide already removed")
    text = guide.read_text(encoding="utf-8")
    live = [m for m in SECRET_RE.findall(text) if "REPLACE" not in m]
    assert live == []
    assert "hf_REPLACE_ME" in text or "your_token" in text.lower() or "hf_xxxx" in text
