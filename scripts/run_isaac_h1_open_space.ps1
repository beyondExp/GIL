param(
  [ValidateSet("goal","external")]
  [string]$WalkerMode = "goal",

  [switch]$AutoPlay = $true,

  [switch]$KillExisting = $true,

  [string]$IsaacSimPath = $env:ISAACSIM_PATH
)

$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path -Parent $PSCommandPath) "_isaac_sim.ps1")

function Resolve-RepoRoot {
  $here = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $here "..")).Path
}

$repoRoot = Resolve-RepoRoot
$launcher = Resolve-IsaacSimLauncher $IsaacSimPath
$isaacRoot = $launcher.Root
$env:ISAACSIM_PATH = $isaacRoot
if (-not $env:OMNI_USER_CACHE) { $env:OMNI_USER_CACHE = "E:\GIL\isaacsim-cache" }
$env:OMNI_KIT_ACCEPT_EULA = "YES"

if ($KillExisting) {
  Stop-ExistingIsaacSim -Force
}

$extFolder = Join-Path $repoRoot "robot_simulator\isaac_exts"
if (-not (Test-Path $extFolder)) {
  throw "Extension folder not found: $extFolder"
}

$autoPlayVal = if ($AutoPlay) { "true" } else { "false" }

$args = @(
  "--ext-folder", $extFolder,
  "--enable", "gil.unitree_h1_scene",
  "--enable", "gil.h1_maze_walker",

  # Disable maze walls for open-space walking
  "--/gil/maze/enabled=false",

  # H1 policy
  "--/gil/humanoid/prefer_h1_policy=true",
  "--/gil/humanoid/variant=h1",

  "--/gil/walker/mode=$WalkerMode",
  "--/gil/walker/prim_path=/World/Humanoid",

  # Conservative walking (open-space demo)
  "--/gil/walker/vx=0.15",
  "--/gil/walker/wz_gain=0.85",
  "--/gil/walker/wz_max=0.50",
  # Extra stability knobs (newer extension versions)
  "--/gil/walker/h1_wz_scale=0.35",
  "--/gil/walker/min_upright_base_z_m=0.55",
  "--/gil/walker/turn_in_place_err=1.05",
  "--/gil/walker/turn_min_vx_scale=0.12",
  "--/gil/walker/stop_radius=0.35",

  "--/gil/walker/require_policy=true",
  "--/gil/walker/force_h1_policy=true",
  "--/gil/walker/fallback_base_velocity=false",
  "--/gil/walker/fallback_simple_gait=false",
  "--/gil/walker/allow_stall_kinematic_fallback=false",

  "--/gil/humanoid/auto_play=$autoPlayVal",
  "--/gil/camera/follow_enabled=false",
  "--/gil/camera/ensure_robot_suite=false",
  "--/gil/camera/capture_all=false"
)

Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] IsaacSim:  $isaacRoot ($($launcher.Kind))"
Write-Host "[GIL] Open-space H1 demo. Mode: $WalkerMode  AutoPlay: $AutoPlay"

& $launcher.Command @args

