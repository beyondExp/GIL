  [string]$Embodiment = $(if ($env:GIL_EMBODIMENT) { $env:GIL_EMBODIMENT } else { "unitree_h1" }),
  [string]$Task = "Isaac-Velocity-Flat-H1-Maze-v0",
  [string]$IsaacSimPath = $env:ISAACSIM_PATH,
  [switch]$NoPolicy,
  [string]$Checkpoint = $env:GIL_POLICY_CHECKPOINT
)

$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path -Parent $PSCommandPath) "_isaac_sim.ps1")

$repoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path
$launcher = Resolve-IsaacSimLauncher $IsaacSimPath
$isaacRoot = $launcher.Root
$env:ISAACSIM_PATH = $isaacRoot
if (-not $env:OMNI_USER_CACHE) { $env:OMNI_USER_CACHE = "E:\GIL\isaacsim-cache" }

# Free the GPU from the headless Docker sim (no Kit viewport on WSL).
try {
  docker compose -f (Join-Path $repoRoot "docker-compose.isaac.yml") stop isaac-sim 2>$null | Out-Null
} catch { }

Stop-ExistingIsaacSim

$env:GIL_ROOT = $repoRoot
$env:GIL_CONTROLS_WS = $(if ($env:GIL_CONTROLS_WS) { $env:GIL_CONTROLS_WS } else { "ws://127.0.0.1:8766" })
$env:GIL_HEADLESS = "0"
$env:GIL_LIVESTREAM = "0"
$env:GIL_USE_POLICY = $(if ($NoPolicy) { "0" } else { "1" })
if ($Checkpoint) { $env:GIL_POLICY_CHECKPOINT = $Checkpoint }
$env:PYTHONPATH = "$repoRoot\src;$($env:PYTHONPATH)"

$play = Join-Path $repoRoot "scripts\isaaclab_play_h1.py"
$pyCandidates = @(
  (Join-Path $isaacRoot "python.bat"),
  (Join-Path $isaacRoot "python.exe"),
  (Join-Path $isaacRoot "kit\python\python.exe"),
  (Join-Path $isaacRoot "Scripts\python.exe")
)
$python = $pyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
  throw "No Isaac Python under $isaacRoot"
}

Write-Host "[GIL] workstation Isaac (native window, not Docker)"
Write-Host "[GIL] Isaac:  $isaacRoot ($($launcher.Kind))"
Write-Host "[GIL] GIL WS: $($env:GIL_CONTROLS_WS)"
Write-Host "[GIL] Keep gil_controls MCP on the host. Heartbeat, enable_humanoid_motion, then drive_humanoid."

cmd.exe /c "`"$python`" -c `"import isaaclab`" 1>nul 2>nul"
$hasLab = ($LASTEXITCODE -eq 0)
# Isaac Lab 2.2 + h5py currently crashes Kit on this Windows pip Isaac (DLL 0xc0000139).
# Default: NVIDIA Kit H1 maze + shipped H1FlatTerrainPolicy (visible cameras/sensors/gait).
$useLab = ($env:GIL_USE_ISAACLAB -eq "1") -and $hasLab
if ($useLab -and ($Embodiment -eq "unitree_h1" -or $Embodiment -eq "h1")) {
  Write-Host "[GIL] Python: $python"
  Write-Host "[GIL] Task:   $Task"
  & $python $play --task $Task
  exit $LASTEXITCODE
}

Write-Host "[GIL] Factory spawn via launch_isaac_robot.ps1 key=$Embodiment"
$launch = Join-Path $PSScriptRoot "launch_isaac_robot.ps1"
& $launch -Key $Embodiment -IsaacSimPath $IsaacSimPath
exit $LASTEXITCODE
