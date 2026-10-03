#!/usr/bin/env bash
set -euo pipefail

export ACCEPT_EULA="${ACCEPT_EULA:-Y}"
export PRIVACY_CONSENT="${PRIVACY_CONSENT:-Y}"
export OMNI_KIT_ACCEPT_EULA="${OMNI_KIT_ACCEPT_EULA:-YES}"
export OMNI_KIT_ALLOW_ROOT="${OMNI_KIT_ALLOW_ROOT:-1}"

# Prefer WSL GPU user-mode libs so Kit/PhysX see the host NVIDIA driver.
if [[ -d /usr/lib/wsl/lib ]]; then
  export LD_LIBRARY_PATH="/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
fi

MODE="${1:-livestream}"
if [[ $# -gt 0 ]]; then
  shift
fi

GIL_ROOT="${GIL_ROOT:-/workspace/gil}"
export PYTHONPATH="${GIL_ROOT}/src:${PYTHONPATH:-}"

play() {
  local task="$1"
  shift || true
  exec python "${GIL_ROOT}/scripts/isaaclab_play_h1.py" --task "${task}" "$@"
}

case "${MODE}" in
  livestream|headless)
    echo "[GIL][container] starting Isaac Sim WebRTC livestream..."
    exec isaacsim --no-window \
      --enable omni.kit.livestream.webrtc \
      --/app/livestream/enabled=true \
      --/app/livestream/port="${ISAACSIM_SIGNAL_PORT:-49100}" \
      "$@"
    ;;
  play-locomotion)
    echo "[GIL][container] Isaac Lab locomotion template Isaac-Velocity-Flat-H1-v0"
    play "Isaac-Velocity-Flat-H1-v0" "$@"
    ;;
  play-maze)
    echo "[GIL][container] Isaac Lab H1 maze locomotion Isaac-Velocity-Flat-H1-Maze-v0"
    play "Isaac-Velocity-Flat-H1-Maze-v0" "$@"
    ;;
  bash|sh)
    exec bash "$@"
    ;;
  *)
    exec "${MODE}" "$@"
    ;;
esac
