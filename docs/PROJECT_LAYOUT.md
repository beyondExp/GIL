# Project layout

## Start here (recommended)

- `README.md`: what GIL is + the safety invariant
- `scripts/bringup.ps1`: single-command bring-up for common demos
- `examples/warehouse/README.md`: canonical “it works” demo

## Key directories

- `src/gil/`: core library (orchestrator, contracts, gates)
- `gil_controls/`: safety supervisor + robot backends + MCP/REST server (default `:6769`)
- `robot_simulator/isaac_exts/`: Isaac Sim extensions (H1 walker, maze env, scene loader)
- `gil_frontend/gods-eye-view/`: inspector UI (Vite dev server, default `:5173`)
- `gil_frontend/gods-eye-view-mcp/`: MCP server for UI automation tools
- `profiles/`: robot profiles and configuration
- `tests/`: core tests + phase gates (`python -m gil.gates`)

## What’s intentionally not part of the runtime

- `Isaac-GR00T/`, `pytorch3d/`, `_third_party/`: reference trees / vendor clones
- `assets/`: local demo artifacts (gitignored)

