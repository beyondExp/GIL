param(
  [ValidateSet("external","goal")]
  [string]$WalkerMode = "external",

  [switch]$AutoPlay = $true,

  [string]$IsaacSimPath = $env:ISAACSIM_PATH
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
  $here = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $here "..")).Path
}

function Resolve-IsaacSimPath([string]$hint) {
  if ($hint -and (Test-Path $hint)) {
    return (Resolve-Path $hint).Path
  }

  $ovPkg = Join-Path $env:LOCALAPPDATA "ov\pkg"
  if (-not (Test-Path $ovPkg)) {
    throw "ISAACSIM_PATH not set and default OV pkg folder not found: $ovPkg"
  }

  $candidates = Get-ChildItem -Path $ovPkg -Directory | Where-Object {
    $_.Name -like "isaac-sim-*" -or $_.Name -like "isaacsim-*"
  } | Sort-Object -Property LastWriteTime -Descending

  if (-not $candidates -or $candidates.Count -eq 0) {
    throw "Could not find an Isaac Sim install under $ovPkg. Set ISAACSIM_PATH to your Isaac Sim install folder."
  }

  return $candidates[0].FullName
}

$repoRoot = Resolve-RepoRoot
$isaacRoot = Resolve-IsaacSimPath $IsaacSimPath

$isaacBat = Join-Path $isaacRoot "isaac-sim.bat"
if (-not (Test-Path $isaacBat)) {
  $alt = Join-Path $isaacRoot "isaac-sim.cmd"
  if (Test-Path $alt) {
    $isaacBat = $alt
  } else {
    throw "Could not find isaac-sim.bat (or .cmd) under: $isaacRoot"
  }
}

$extFolder = Join-Path $repoRoot "robot_simulator\isaac_exts"
if (-not (Test-Path $extFolder)) {
  throw "Extension folder not found: $extFolder"
}

# IMPORTANT: Do NOT set GIL_PREFER_H1_2 / GIL_HUMANOID_USD_REL here.
# We want the Isaac-shipped Unitree H1 USD so the built-in H1 locomotion policy works.

$autoPlayVal = if ($AutoPlay) { "true" } else { "false" }

$args = @(
  "--ext-folder", $extFolder,
  "--enable", "isaacsim.ros2.bridge",
  "--enable", "gil.unitree_h1_scene",
  "--enable", "gil.h1_maze_walker",

  # Force the scene spawner to pick H1 from Isaac assets root (policy-compatible).
  "--/gil/humanoid/prefer_h1_policy=true",
  "--/gil/humanoid/variant=h1",

  "--/gil/walker/mode=$WalkerMode",
  "--/gil/walker/prim_path=/World/Humanoid",

  # REAL locomotion only: require policy; disable all cheats.
  "--/gil/walker/require_policy=true",
  "--/gil/walker/force_h1_policy=true",
  "--/gil/walker/fallback_base_velocity=false",
  "--/gil/walker/fallback_simple_gait=false",
  "--/gil/walker/allow_stall_kinematic_fallback=false",

  "--/gil/humanoid/auto_play=$autoPlayVal",
  "--/gil/camera/follow_enabled=false",
  # Enable additional robot cameras so MCP can return multi-view images (see `images` map).
  "--/gil/camera/ensure_robot_suite=true",
  "--/gil/camera/capture_all=true"
)

Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] IsaacSim:  $isaacRoot"
Write-Host "[GIL] Mode:      $WalkerMode  AutoPlay: $AutoPlay"
Write-Host "[GIL] Locomotion: H1FlatTerrainPolicy (require_policy=true)"

& $isaacBat @args


