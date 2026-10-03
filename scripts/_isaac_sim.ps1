# Shared Isaac Sim path resolution. Dot-source from launch scripts.
# Supports the NVIDIA pip package (E:\GIL\isaacsim) and the older OV kit layout.

function Resolve-IsaacSimLauncher {
  param([string]$Hint)

  $roots = @()
  if ($Hint) { $roots += $Hint }
  if ($env:ISAACSIM_PATH) { $roots += $env:ISAACSIM_PATH }
  $roots += "E:\GIL\isaacsim"

  $ovPkg = Join-Path $env:LOCALAPPDATA "ov\pkg"
  if (Test-Path $ovPkg) {
    $roots += @(Get-ChildItem -Path $ovPkg -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like "isaac-sim-*" -or $_.Name -like "isaacsim-*" } |
      Sort-Object LastWriteTime -Descending |
      ForEach-Object { $_.FullName })
  }

  foreach ($root in $roots) {
    if (-not $root) { continue }
    if (-not (Test-Path $root)) { continue }
    $resolved = (Resolve-Path $root).Path
    $bat = Join-Path $resolved "isaac-sim.bat"
    $cmd = Join-Path $resolved "isaac-sim.cmd"
    $pipExe = Join-Path $resolved "Scripts\isaacsim.exe"
    if (Test-Path $bat) {
      return [pscustomobject]@{ Kind = "kit"; Command = $bat; Root = $resolved }
    }
    if (Test-Path $cmd) {
      return [pscustomobject]@{ Kind = "kit"; Command = $cmd; Root = $resolved }
    }
    if (Test-Path $pipExe) {
      return [pscustomobject]@{ Kind = "pip"; Command = $pipExe; Root = $resolved }
    }
  }

  throw "Isaac Sim not found. Install with scripts\install_isaac_sim.ps1 or set ISAACSIM_PATH (expected E:\GIL\isaacsim)."
}


function Stop-ExistingIsaacSim {
  param(
    [switch]$Force = $true,
    [int]$WaitSeconds = 2
  )

  # Isaac Sim (pip) runs as a python process with a visible main window title.
  # Keep this conservative to avoid killing unrelated python jobs.
  $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowTitle -and $_.MainWindowTitle -like "Isaac Sim*"
  })

  if ($procs.Count -le 0) { return }

  Write-Host "[GIL] Stopping existing Isaac Sim instance(s): $($procs.Id -join ', ')"
  foreach ($p in $procs) {
    try {
      if ($Force) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
      } else {
        Stop-Process -Id $p.Id -ErrorAction SilentlyContinue
      }
    } catch { }
  }

  if ($WaitSeconds -gt 0) {
    Start-Sleep -Seconds $WaitSeconds
  }
}
