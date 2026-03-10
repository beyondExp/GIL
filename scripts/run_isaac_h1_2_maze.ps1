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
  # Some installs use a different entry point name
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

$h12Usd = Join-Path $repoRoot "_third_party\unitree_sim_isaaclab\assets\robots\h1_2-26dof-inspire-base-fix-usd\h1_2_26dof_with_inspire_rev_1_0.usd"
if (-not (Test-Path $h12Usd)) {
  throw "H1-2 USD not found (did you fetch Unitree assets?): $h12Usd"
}

$sensorsCfg = Join-Path $repoRoot "robot_simulator\config\robots\unitree_h1_2_sensors.json"
if (-not (Test-Path $sensorsCfg)) {
  throw "H1-2 sensor config not found: $sensorsCfg"
}

# Environment variables consumed by gil.unitree_h1_scene
$env:GIL_PREFER_H1_2 = "1"
$env:GIL_HUMANOID_USD_REL = $h12Usd
$env:GIL_ROBOT_SENSORS_CONFIG = $sensorsCfg

# We also keep /World/Humanoid as the prim path across all extensions.
$autoPlayVal = if ($AutoPlay) { "true" } else { "false" }

$args = @(
  "--ext-folder", $extFolder,
  # Enable Isaac Sim's ROS2 bridge so our extensions can create rclpy nodes inside Isaac.
  "--enable", "isaacsim.ros2.bridge",
  "--enable", "gil.unitree_h1_scene",
  "--enable", "gil.h1_maze_walker",
  # Settings (carb settings)
  "--/gil/humanoid/variant=h1_2",
  "--/gil/walker/mode=$WalkerMode",
  "--/gil/walker/prim_path=/World/Humanoid",
  # Explicitly disable the built-in H1 policy for H1-2 (it is usually incompatible in Isaac 5.1).
  "--/gil/walker/force_h1_policy=false",
  "--/gil/humanoid/auto_play=$autoPlayVal",
  # Default to a static camera so motion is visible
  "--/gil/camera/follow_enabled=false"
)

Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] IsaacSim:  $isaacRoot"
Write-Host "[GIL] RobotUSD:  $h12Usd"
Write-Host "[GIL] Sensors:   $sensorsCfg"
Write-Host "[GIL] Mode:      $WalkerMode  AutoPlay: $AutoPlay"

& $isaacBat @args



