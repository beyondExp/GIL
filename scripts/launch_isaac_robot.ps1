param(
  [string]$Key = $(if ($env:GIL_EMBODIMENT) { $env:GIL_EMBODIMENT } else { "unitree_h1" }),
  [string]$IsaacSimPath = $env:ISAACSIM_PATH,
  [switch]$AllowKitFallback = $false
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path
$maze = Join-Path $PSScriptRoot "run_isaac_h1_maze_real.ps1"
$labPlay = Join-Path $PSScriptRoot "isaaclab_play_h1.py"
$h12Usd = Join-Path $repoRoot "_third_party\unitree_sim_isaaclab\assets\robots\h1_2-26dof-inspire-base-fix-usd\h1_2_26dof_with_inspire_rev_1_0.usd"

$needle = ($Key.Trim().ToLower() -replace " ", "_" -replace "-", "_")

Write-Host "[GIL] launch_isaac_robot key=$needle"

. (Join-Path $PSScriptRoot "_isaac_sim.ps1")
$launcher = Resolve-IsaacSimLauncher $IsaacSimPath
$env:ISAACSIM_PATH = $launcher.Root
if (-not $env:OMNI_USER_CACHE) { $env:OMNI_USER_CACHE = "E:\GIL\isaacsim-cache" }
Stop-ExistingIsaacSim -Force

if ($needle -in @("unitree_h1_2", "h1_2", "h12")) {
  if (-not (Test-Path $h12Usd)) {
    throw "H1-2 factory USD missing: $h12Usd (fetch Unitree isaaclab assets into _third_party/unitree_sim_isaaclab)"
  }
  & $maze -HumanoidVariant h1_2 -UseLabRobot -CameraCapture -CameraCollage -KillExisting -IsaacSimPath $IsaacSimPath
  exit $LASTEXITCODE
}

if ($needle -in @("unitree_g1", "g1")) {
  if ($env:GIL_USE_ISAACLAB -ne "1") {
    if (-not $AllowKitFallback) {
      throw "Factory G1 is Isaac Lab task Isaac-Velocity-Flat-G1-v0. Set GIL_USE_ISAACLAB=1 and run isaaclab_play_h1.py, or pass -AllowKitFallback for the broken Kit overlay."
    }
    Write-Host "[GIL] WARNING: Kit G1 overlay is not factory sensors/policy."
    & $maze -HumanoidVariant g1 -CameraCapture -CameraCollage -KillExisting -IsaacSimPath $IsaacSimPath
    exit $LASTEXITCODE
  }
  $pyCandidates = @(
    (Join-Path $launcher.Root "python.bat"),
    (Join-Path $launcher.Root "python.exe"),
    (Join-Path $launcher.Root "kit\python\python.exe"),
    (Join-Path $launcher.Root "Scripts\python.exe")
  )
  $python = $pyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $python) { throw "No Isaac Python under $($launcher.Root)" }
  $env:PYTHONPATH = "$repoRoot\src;$($env:PYTHONPATH)"
  $env:ENABLE_CAMERAS = "0"
  & $python $labPlay --task Isaac-Velocity-Flat-G1-v0
  exit $LASTEXITCODE
}

if ($needle -in @("franka", "panda", "franka_panda")) {
  if ($env:GIL_USE_ISAACLAB -ne "1") {
    throw "Factory camera tasks require Isaac Lab. Set GIL_USE_ISAACLAB=1."
  }
  $pyCandidates = @(
    (Join-Path $launcher.Root "python.bat"),
    (Join-Path $launcher.Root "python.exe"),
    (Join-Path $launcher.Root "kit\python\python.exe"),
    (Join-Path $launcher.Root "Scripts\python.exe")
  )
  $python = $pyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $python) { throw "No Isaac Python under $($launcher.Root)" }
  $env:PYTHONPATH = "$repoRoot\src;$($env:PYTHONPATH)"
  $env:GIL_USE_POLICY = "0"
  $env:ENABLE_CAMERAS = "1"
  & $python $labPlay --task Isaac-Stack-Cube-Franka-IK-Rel-Visuomotor-v0 --num_envs 1
  exit $LASTEXITCODE
}

# Default: NVIDIA H1 USD + H1FlatTerrainPolicy + Lab-style FPV/chase (unitree_h1_sensors.json).
& $maze -HumanoidVariant h1 -ForcePolicy -CameraCapture -CameraCollage -KillExisting -IsaacSimPath $IsaacSimPath
exit $LASTEXITCODE
