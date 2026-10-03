param(
  [ValidateSet("goal","external")]
  [string]$WalkerMode = "goal",

  [string]$RobotType = "humanoid_biped",
  [int]$Limit = 6,

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
$isaacLauncher = Join-Path $repoRoot "scripts\\run_isaac_h1_open_space.ps1"
$runner = Join-Path $repoRoot "scripts\\run_curriculum_stages.py"

if (-not (Test-Path $controlsMain)) { throw "Missing: $controlsMain" }
if (-not (Test-Path $isaacLauncher)) { throw "Missing: $isaacLauncher" }
if (-not (Test-Path $runner)) { throw "Missing: $runner" }

# Controls defaults (sim websocket backend).
$env:GIL_USE_ROS2 = "0"
$env:GIL_HUMANOID_BACKEND = "sim_ws"

Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] Starting Isaac Sim (open space) ..."
Write-Host "[GIL] Starting gil_controls MCP server ..."
Write-Host "[GIL] Then running curriculum stages: robot_type=$RobotType limit=$Limit"
Write-Host "[GIL] Ollama (optional): set OLLAMA_HOST and OLLAMA_MODEL; ensure `ollama serve` is running."

# 1) Start gil_controls (port 6769 + ws server 8766)
$controlsProc = Start-Process -FilePath "python" -ArgumentList @("$controlsMain") -WorkingDirectory $controlsDir -PassThru

# 2) Start Isaac Sim in a separate PowerShell process (UI app)
$isaacArgs = @(
  "-ExecutionPolicy", "Bypass",
  "-File", $isaacLauncher,
  "-WalkerMode", $WalkerMode
)
if ($IsaacSimPath) { $isaacArgs += @("-IsaacSimPath", $IsaacSimPath) }
$isaacProc = Start-Process -FilePath "powershell" -ArgumentList $isaacArgs -WorkingDirectory $repoRoot -PassThru

# 3) Wait for MCP port to open
Write-Host "[GIL] Waiting for MCP port 6769 ..."
$t0 = Get-Date
while ($true) {
  $ok = $false
  try {
    $tnc = Test-NetConnection -ComputerName "127.0.0.1" -Port 6769 -WarningAction SilentlyContinue
    $ok = [bool]$tnc.TcpTestSucceeded
  } catch { $ok = $false }
  if ($ok) { break }
  if (((Get-Date) - $t0).TotalSeconds -gt 25) { throw "Timed out waiting for MCP port 6769." }
  Start-Sleep -Milliseconds 400
}

# 4) Run curriculum stages (in this terminal)
Push-Location $repoRoot
try {
  & python $runner --robot_type $RobotType --limit $Limit
} finally {
  Pop-Location
  Write-Host "[GIL] Done. If you want to stop background processes:"
  Write-Host "  - Stop gil_controls: Stop-Process -Id $($controlsProc.Id)"
  Write-Host "  - Stop Isaac Sim:    Stop-Process -Id $($isaacProc.Id)"
}

