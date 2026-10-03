# GIL - General Intelligence Layer

GIL is a safety-gated robot control stack with a dream-then-act loop.

An agent talks to one orchestrator. The orchestrator copies the scene, imagines rollouts, trains or selects a plan, and **only then** asks controls to move. World models never publish `cmd_vel`.

## What is live

| Piece | Role | Default |
|---|---|---|
| `gil_controls` | Body: ROS2 / WebSocket / H1 backends, enable, estop, heartbeat | `:6769` |
| `gil_models` | Perception VLM helpers (not motion authority) | `:6770` |
| `src/gil` | Production core: profiles, orchestrator, map, dream/critic, LeRobot writer, A2A cards | library |
| `robot_simulator/isaac_exts` | **Live viewport and PhysX body**: H1 maze walker in Isaac Sim | `scripts/run_isaac_h1_maze_real.ps1` |

`gil_frontend` (Three.js) is retired as a robot. Do not connect it to `:8766` while Isaac is running.

Imagination copies the Isaac maze (`gil.world.maze3d`) and rolls a kinematic twin. The agent (`steer`) picks an Isaac body, ingests a world from text/image/video, instructs the skill, previews the dream in Isaac as `preview_vel`, then executes `cmd_vel` only if the gate passes. Marble / NuRec / Cosmos Transfer are the next USD/camera generators — not wired yet, and they never publish motors.

Product docs: `docs/README.md` and `docs/steering_intelligence_stages.md`. Archived HuggingFace-MCP / session dumps live under `docs/archive/`. Third-party clones (`Isaac-GR00T`, `pytorch3d`, `_third_party`) are reference trees, not GIL runtime.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

Optional (large) vendor deps are kept as git submodules:

```bash
git submodule update --init --recursive
```

## Phase gates

Every production phase has tests. Run them in order:

```bash
python -m gil.gates
# or
pytest -m phase0
```

## Sim bring-up

Isaac Sim is the robot. `gil_controls` must be up first so the walker can connect to `ws://127.0.0.1:8766`.

## Quickstart (Windows)

One command brings up a working demo stack (controls + Isaac + God's Eye View):

```powershell
.\scripts\bringup.ps1 -Demo warehouse
```

See: `examples/warehouse/README.md`.

```bash
python gil_controls/src/main.py
```

```powershell
.\scripts\run_isaac_h1_maze_real.ps1
```

Connect an MCP client to `http://127.0.0.1:6769/mcp/`. After a passing gate:

```bash
python scripts/watch_gated_mission.py --live
```

Prefer the orchestrator library (`gil.orchestrator.Orchestrator`) over calling models by hand. World models never talk to motors.

## Safety invariant

Models propose. `gil_controls` plus `SafetySupervisor` permit. Vision payloads with `safe_for_motion_authority: false` cannot become motor commands. Hardware mode requires a token, preflight, calibrated `camera_info` before autonomy, and a limp-speed first move.

## Robot profiles

JSON files in `profiles/`. New robot = new profile + backend, not a new MCP server.

- `arm_sim`
- `unitree_h1_sim`
- `unitree_h1_hardware`
