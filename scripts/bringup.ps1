param(
  [ValidateSet("warehouse","maze","open_space","none")]
  [string]$Demo = "warehouse",

  # Start/stop toggles
  [bool]$StartControls = $true,
  [bool]$StartIsaac = $true,
  [bool]$StartUi = $true,

  # World anchor for demos that use God's Eye View
  [double]$Lon = 8.563542482184204,
  [double]$Lat = 47.440513741568296,
  [double]$Yaw = 0.0,

  [string]$IsaacSimPath = $env:ISAACSIM_PATH
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
  $here = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $here "..")).Path
}

$repoRoot = Resolve-RepoRoot
$controlsDir = Join-Path $repoRoot "gil_controls"
$controlsMain = Join-Path $controlsDir "src\\main.py"

$warehouse = Join-Path $repoRoot "scripts\\demo_warehouse_world.ps1"
$maze = Join-Path $repoRoot "scripts\\run_isaac_h1_maze_real.ps1"
$openSpace = Join-Path $repoRoot "scripts\\run_isaac_h1_open_space.ps1"
$uiDir = Join-Path $repoRoot "gil_frontend\\gods-eye-view"

Write-Host "[GIL] bringup demo=$Demo start_controls=$StartControls start_isaac=$StartIsaac start_ui=$StartUi"

if ($StartControls) {
  if (-not (Test-Path $controlsMain)) { throw "Missing: $controlsMain" }
  $env:GIL_USE_ROS2 = "0"
  $env:GIL_HUMANOID_BACKEND = "sim_ws"
  Write-Host "[GIL] Starting gil_controls (sim_ws) ..."
  $controlsProc = Start-Process -FilePath "python" -ArgumentList @("$controlsMain") -WorkingDirectory $controlsDir -PassThru
}

# Start UI early so it can hot-reload while Isaac boots
if ($StartUi) {
  if (-not (Test-Path $uiDir)) { throw "Missing UI dir: $uiDir" }
  Write-Host "[GIL] Starting God's Eye View dev server (http://127.0.0.1:5173/) ..."
  Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoProfile","-ExecutionPolicy","Bypass",
    "-Command", "cd `"$uiDir`"; npm run dev -- --host 127.0.0.1 --port 5173"
  ) -WorkingDirectory $repoRoot | Out-Null
}

if ($StartIsaac) {
  if ($Demo -eq "warehouse") {
    if (-not (Test-Path $warehouse)) { throw "Missing: $warehouse" }
    & $warehouse -Lon $Lon -Lat $Lat -Yaw $Yaw -StartControls:$false -StartIsaac:$true -IsaacSimPath $IsaacSimPath
  } elseif ($Demo -eq "maze") {
    if (-not (Test-Path $maze)) { throw "Missing: $maze" }
    # Maze demo: controls must already be up for ws://127.0.0.1:8766
    & $maze -Kit base -HumanoidVariant h1 -ForcePolicy -CameraCapture -CameraCollage -KillExisting -IsaacSimPath $IsaacSimPath
  } elseif ($Demo -eq "open_space") {
    if (-not (Test-Path $openSpace)) { throw "Missing: $openSpace" }
    & $openSpace -IsaacSimPath $IsaacSimPath
  } else {
    Write-Host "[GIL] Demo=none: skipping Isaac launch."
  }
}

Write-Host "[GIL] Ready:"
Write-Host "  - gil_controls:  http://127.0.0.1:6769 (MCP at /mcp/)"
if ($StartUi) {
  Write-Host "  - God's Eye View: http://127.0.0.1:5173/"
}
Write-Host "  - Tip: in God's Eye View enable GIL Robot panel and press FOCUS."

