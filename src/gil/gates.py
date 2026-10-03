from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PHASES = [f"phase{i}" for i in range(9)]
REPO_ROOT = Path(__file__).resolve().parents[2]


def run_phase(phase: str) -> int:
    cmd = [sys.executable, "-m", "pytest", "-m", phase]
    print(f"\n=== GIL gate {phase} ===")
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run GIL production phase gates in order.")
    parser.add_argument("--from", dest="start", default="phase0")
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    selected = args.only or PHASES[PHASES.index(args.start) :]
    for phase in selected:
        code = run_phase(phase)
        if code != 0:
            print(f"GATE FAILED: {phase}")
            return code
        print(f"GATE PASSED: {phase}")
    print("All requested GIL phase gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
