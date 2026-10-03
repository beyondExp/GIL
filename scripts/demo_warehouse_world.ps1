param(
  # Where to pin the simulated warehouse on the real-world globe (God's Eye View).
  # Default: the coordinate you requested (Zurich-ish).
  [double]$Lon = 8.563542482184204,
  [double]$Lat = 47.440513741568296,
  [double]$Height = 0.0,
  # Robot yaw in degrees in the local ENU frame (0 = facing East in our localToWorld).
  [double]$Yaw = 0.0,

  # Start gil_controls automatically (recommended for the demo).
  [switch]$StartControls = $true,

  # Start Isaac Sim automatically.
  [switch]$StartIsaac = $true,

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
$isaacLauncher = Join-Path $repoRoot "scripts\\run_isaac_h1_maze_real.ps1"

if ($StartControls) {
  if (-not (Test-Path $controlsMain)) { throw "Missing: $controlsMain" }
  # Controls defaults for Isaac websocket backend.
  $env:GIL_USE_ROS2 = "0"
  $env:GIL_HUMANOID_BACKEND = "sim_ws"
  Write-Host "[GIL] Starting gil_controls (sim_ws) ..."
  $controlsProc = Start-Process -FilePath "python" -ArgumentList @("$controlsMain") -WorkingDirectory $controlsDir -PassThru
}

if ($StartIsaac) {
  if (-not (Test-Path $isaacLauncher)) { throw "Missing: $isaacLauncher" }
  Write-Host "[GIL] Starting Isaac Sim (EnvPreset=warehouse, maze disabled) ..."
  $isaacArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $isaacLauncher,
    "-Kit", "base",
    "-MazeEnabled", "0",
    "-EnvPreset", "warehouse",
    "-ForcePolicy",
    "-CameraCapture",
    "-CameraCollage",
    "-KillExisting"
  )
  if ($IsaacSimPath) { $isaacArgs += @("-IsaacSimPath", $IsaacSimPath) }
  $isaacProc = Start-Process -FilePath "powershell" -ArgumentList $isaacArgs -WorkingDirectory $repoRoot -PassThru
}

Write-Host "[GIL] Waiting for gil_controls on port 6769 ..."
$t0 = Get-Date
while ($true) {
  $ok = $false
  try {
    $tnc = Test-NetConnection -ComputerName "127.0.0.1" -Port 6769 -WarningAction SilentlyContinue
    $ok = [bool]$tnc.TcpTestSucceeded
  } catch { $ok = $false }
  if ($ok) { break }
  if (((Get-Date) - $t0).TotalSeconds -gt 30) { throw "Timed out waiting for gil_controls (6769)." }
  Start-Sleep -Milliseconds 400
}

# Pin the sim's local frame to a real-world lon/lat for God’s Eye View.
Write-Host ("[GIL] Setting world anchor lon={0} lat={1} yaw={2} ..." -f $Lon, $Lat, $Yaw)
$payload = @{
  robot_kind = "humanoid"
  lon = [double]$Lon
  lat = [double]$Lat
  height = [double]$Height
  yaw = [double]$Yaw
  z = 1.05
} | ConvertTo-Json

try {
  $res = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:6769/api/spawn_world" -ContentType "application/json" -Body $payload
  Write-Host ("[GIL] spawn_world ok={0} anchor=({1},{2})" -f $res.ok, $res.anchor.lon, $res.anchor.lat)
} catch {
  Write-Host "[GIL] WARNING: spawn_world failed: $($_.Exception.Message)"
}

Write-Host "[GIL] Next:"
Write-Host "  - Open God's Eye View, enable GIL Robot panel, press FOCUS."
Write-Host "  - If you want to stop processes:"
if ($StartControls -and $controlsProc) { Write-Host "    - Stop gil_controls: Stop-Process -Id $($controlsProc.Id)" }
if ($StartIsaac -and $isaacProc) { Write-Host "    - Stop Isaac:       Stop-Process -Id $($isaacProc.Id)" }

