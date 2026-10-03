"""Shared test configuration. Prevents tests from writing to the user home directory."""
from __future__ import annotations

import os

os.environ.setdefault("GIL_COMPETENCE_PATH", ":memory:")
