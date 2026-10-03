# Getting started (workstation)

GIL’s live robot is **Isaac Sim** plus **gil_controls**. An agent talks MCP; the orchestrator dreams on a scene copy and only then may send `cmd_vel`. World models never publish motors.

## 1. Controls

```bash
python gil_controls/src/main.py
```

MCP: `http://127.0.0.1:6769/mcp/`  
WebSocket for Isaac: `ws://127.0.0.1:8766`

## 2. Isaac (native window)

```powershell
.\scripts\run_gil_workstation.ps1
```

This launches official **Unitree H1** + `H1FlatTerrainPolicy` with **FPV capture only**. Do not enable CaptureAll on this Windows/Vulkan path (Kit UI freezes).

Keep `gil_controls` running first so the walker can connect.

## 3. Agent loop

After preflight:

1. `list_embodiments` / `select_embodiment` — H1 USD, not H1-2 with the H1 policy.
2. `ingest_world` / `instruct` — goal and skill.
3. `get_steering_status` — ladder A must be ready.
4. `steer` (no commit) — dream; learn if the skill is not competent.
5. `preview_plan` (optional).
6. `steer(..., commit=True)` only if the gate and competence pass.

Heartbeat, `enable_humanoid_motion`, then motors. Heartbeat timeout is 2s.

Raise walk/run speed only via `GIL_MAX_VX` (default 0.6). Do not remove the clamp in code for a demo.

## 4. Tests

```bash
python -m gil.gates
```
