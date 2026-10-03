# Steering intelligence: stages

This is the **process the agent must walk** before it moves a robot, and again when a skill is missing. The product goal is: *tell the robot to go somewhere (or do a skill); if it cannot yet, it trains that skill in imagination first; only then it executes.*

Code today already has pieces of this:

- Skill catalog: `src/gil/learn/curriculum.py`
- One-shot agent loop: `AgentDirector.steer()` in `src/gil/orchestrator/director.py`
- Execute gate: `src/gil/orchestrator/gate.py`

What this document adds is a **stage machine** the agent owns: it must decide *which stage it is in*, *whether that stage is ready*, and *refuse to skip*.

---

## Two ladders (do not mix them)

| Ladder | Question | Advances when |
| --- | --- | --- |
| **A. Body + world readiness** | Can we even think and move safely *right now*? | Hardware, sim, sensors, safety, and a parsed goal are live |
| **B. Skill competence** | Can this body do *this instruction*? | Curriculum stage for that skill is **passed**, or a learn loop has just passed it |

An instruction like “walk to the exit” is **not** allowed to jump to motors. The agent maps it to a skill stage (usually `local_goal_reaching` or `maze_escape`), then checks ladder A, then ladder B. If B fails, it enters **learn**, not **execute**.

---

## Agent rule

For every stage:

1. **Observe** the checks for this stage.
2. **Decide**: `ready` | `blocked` | `learn` | `unsafe`.
3. **Act** only inside that stage (never execute from a blocked or learn stage).
4. **Record** the decision (stage id, checks, reason). This is the audit trail.

`unsafe` always wins: e-stop, heartbeat timeout, unknown body, or critic “do not harm”.

---

## Ladder A — Body and world (runtime)

These stages are **serial**. The agent does not dream or drive until A6.

### A0 — Intent

**Purpose.** Turn language into a goal, constraints, and a *target skill* from the curriculum.

**Ready when.** Instruction parsed; goal has pose or named place; skill id chosen (e.g. `local_goal_reaching`); agent can say what “done” means (radius, timeout, no-fall).

**If not ready.** Ask to clarify, or refuse vague/unsafe commands. This is curriculum `language_grounding`.

### A1 — Embodiment

**Purpose.** Know which body we are (H1 vs H1-2 vs arm), which USD, which locomotion (policy vs fallback).

**Ready when.** Embodiment selected; Isaac stage matches that USD; policy/controller that *belongs to that body* is loaded (H1 → `H1FlatTerrainPolicy`, not an H1-2 Lab USD).

**If not ready.** `select_embodiment` / reload stage. Do not invent a gait on the wrong asset.

### A2 — World

**Purpose.** Have a scene the world model can copy (maze, room, occupancy).

**Ready when.** World ingested; map exists; live sim matches the spec (or the spec is marked synthetic and we are not claiming live).

**If not ready.** `ingest_world`. Do not dream on an empty map.

### A3 — Sensing

**Purpose.** State the critic and learner will trust.

**Ready when.** Base pose is updating; FPV (or the agreed camera set) is producing frames; IMU/contacts are attached *or* explicitly marked unavailable so the critic does not pretend they exist.

**If not ready.** Stay here. Do not treat a frozen viewport or empty `imu` as calibrated proprioception (`proprioception_calibration`).

### A4 — Safety envelope

**Purpose.** Authority to move at all.

**Ready when.** Controls connected; preflight ok; e-stop clear; heartbeat fresh; motion enable is a *later* step, not this one. Speed caps for the current skill are known (today GIL clamps `vx` via `GIL_MAX_VX`).

**If not ready.** Heartbeat / preflight / wait. Never `drive_humanoid`.

### A5 — Stance (humanoid)

**Purpose.** Body is in a state where locomotion skills apply.

**Ready when.** Upright, not fallen, physics playing. Maps to `stand_idle` (and `stand_up_from_fall` if down).

**If not ready.** Recover or reset episode. Do not command walk on a fallen robot.

### A6 — Imagination allowed

**Purpose.** Permission to **dream** (copy of the Isaac scene, no motors).

**Ready when.** A0–A5 passed.

**Act.** `dream` + critic + confidence gate. Output is a kept plan or a refuse reason. **Still no `cmd_vel`.**

---

## Ladder B — Skill competence (curriculum)

These stages are the **capabilities** the robot may already have (pretrained policy) or must **learn**. The agent picks the **minimum stage that satisfies the instruction**, then requires all prerequisites to be `passed`.

For **humanoid biped** (Unitree H1), order is:

| Stage id | What “passed” means (intent) | Typical instruction |
| --- | --- | --- |
| `safety_do_no_harm` | Obeys e-stop; stays in envelope | — |
| `proprioception_calibration` | Pose / contacts / IMU usable | — |
| `stand_idle` | Holds upright | “just stand” |
| `balance_recovery` | Recovers from light push | — |
| `stand_up_from_fall` | Get-up after fall | — |
| `locomotion_forward` | Walks ~1 m, no fall | “walk forward” |
| `stop_and_hold` | Stops and stays up | “stop” |
| `locomotion_turning` | Turns without tipping | “turn left” |
| `collision_avoidance` | Does not drive into walls | — |
| `local_goal_reaching` | Reaches a nearby (x, y) | “go over there” |
| `maze_escape` | Reaches exit in tight space | “escape the maze” |
| `language_grounding` | Instruction → goal | (A0) |
| `instruction_following_skills` | One skill from language | “walk to the door” |
| `instruction_following_natural_env` | Multi-step in a real scene | later |

Full metric names live in `curriculum.py`. Other robot types use a **subset** of the same catalog.

**Competence check (agent):** for the target skill, run a **short eval** (dream rollouts and/or a bounded live trial under the gate). If success metrics fail → ladder **L** (learn). If they pass → ladder **E** (execute).

---

## Ladder L — Learn if not able

Used only when A6 is allowed and B says **not competent**.

### L1 — Isolate the gap

Name the **first failed prerequisite** (do not train maze escape if the robot cannot walk).

### L2 — Dream / train that stage only

Imagine rollouts (and later: fine-tune a policy, or collect demos) **for that stage’s metrics**. World model does **not** send `cmd_vel`.

### L3 — Critic + gate on the skill

Same confidence gate as execute: imagination success, critic score, map, preflight. Fail → stay in L2 or drop to a simpler stage.

### L4 — Optional bounded live probe

If dreams look good, a **short, capped** live trial (heartbeat on, speed cap of *this* stage, e.g. walk `vx_cap` 0.15). Fall or collision → fail the stage, back to L2.

### L5 — Mark passed / persist

Write competence: `robot + env + stage = passed` (memory / checkpoint). Then return to B for the original instruction.

The agent **does not** skip from L2 to full maze execute.

---

## Ladder E — Execute (motors)

Only after A6 **and** B **passed** for the instruction’s skill.

### E1 — Enable motion

Heartbeat + `enable_humanoid_motion`. Timeout → drop to A4.

### E2 — Preview (optional)

Replay the kept plan as `preview_vel` in Isaac, then reset. Human or agent can abort.

### E3 — Commit

`steer(..., commit=True)` / `run_mission` only if `gate.ok`. Commands go through GIL controls, not the world model.

### E4 — Monitor and terminate

Success metrics of the skill, or abort: fall, e-stop, heartbeat loss, collision. Then `stop_and_hold` or disable motion.

---

## Example: “Walk to the far corner”

```
A0  parse → goal (x,y), skill = local_goal_reaching
A1  H1 USD + H1FlatTerrainPolicy
A2  maze ingested
A3  pose + FPV live
A4  preflight, heartbeat
A5  standing
A6  dream paths to (x,y)
B   is local_goal_reaching passed?
      no → L1 first gap (e.g. locomotion_turning)
           L2–L5 until passed
           re-check B
      yes → E1 enable → E3 drive → E4 stop at radius
```

If the shipped H1 policy already walks, L may be **empty** for `locomotion_forward` and the agent spends time on **goal reaching / maze** instead of relearning gait.

---

## What the agent must never do

- Execute because the user said “go” while A or B is not ready.
- Learn maze escape before walk/turn/stop are passed.
- Swap H1-2 Lab cameras/USD onto the H1 policy body and call that “ready.”
- Treat a frozen Kit UI or CaptureAll hang as “sensors ready.”
- Let the world model issue `cmd_vel`.

---

## Mapping to tools (today)

| Stage | Tools / code |
| --- | --- |
| A0 | `instruct` |
| A1 | `list_embodiments`, `select_embodiment` |
| A2 | `ingest_world` |
| A3–A5 | `get_health`, `get_robot_state`, `run_humanoid_preflight` |
| A6 / L2 | `dream`, `preview_plan` |
| E1–E3 | `send_humanoid_heartbeat`, `enable_humanoid_motion`, `drive_humanoid` / `steer(commit=True)` |
| B catalog | `curriculum_for(robot_type)` on `list_embodiments` / `steer` |

`steer()` today fills A1–A2–A0–A6 in one call and **does not yet** run L (learn-if-unable) or persist stage pass/fail. That is the intelligence gap this stage list is meant to close.

---

## Status of each stage (honest)

| Area | Status |
| --- | --- |
| Catalog of B stages | Defined in code |
| A1–A6 as a *forced* state machine | Partial (`steer` shortcuts; no persistent stage id) |
| L learn-if-unable | **Not implemented** (dream exists; no “failed skill → train that stage → retry”) |
| E gated execute | Gate exists; live H1 walk is policy + `cmd_vel` |
| Memory of passed stages | Episode memory exists; not wired as competence ledger |

Next conversation: which stage to implement first (likely **competence check + L1–L5 for `local_goal_reaching`**, with A0–A6 as hard gates).
