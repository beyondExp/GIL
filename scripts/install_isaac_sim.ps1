param(
  [string]$Prefix = "E:\GIL\isaacsim",
  [string]$Version = "5.1.0"
)

$ErrorActionPreference = "Stop"

$cacheRoot = "E:\GIL"
New-Item -ItemType Directory -Force -Path @(
  $cacheRoot,
  "$cacheRoot\tmp",
  "$cacheRoot\pip-cache",
  "$cacheRoot\conda-pkgs",
  "$cacheRoot\hf-worlds"
) | Out-Null

$env:TEMP = "$cacheRoot\tmp"
$env:TMP = "$cacheRoot\tmp"
$env:PIP_CACHE_DIR = "$cacheRoot\pip-cache"
$env:CONDA_PKGS_DIRS = "$cacheRoot\conda-pkgs"
$env:HF_HOME = "E:\hf_home"

if (-not (Test-Path "$Prefix\python.exe")) {
  Write-Host "[GIL] Creating conda env at $Prefix (Python 3.11)"
  conda create -p $Prefix python=3.11 pip -y
}

Write-Host "[GIL] Installing isaacsim[$Version] into $Prefix"
& "$Prefix\python.exe" -m pip install --upgrade pip
& "$Prefix\python.exe" -m pip install "isaacsim[all,extscache]==$Version" --extra-index-url https://pypi.nvidia.com
if ($LASTEXITCODE -ne 0) { throw "isaacsim pip install failed: $LASTEXITCODE" }

Write-Host "[GIL] Verifying import"
& "$Prefix\python.exe" -c "import isaacsim; print('isaacsim ok', getattr(isaacsim, '__file__', ''))"
Write-Host "[GIL] Set ISAACSIM_PATH=$Prefix before launching scripts\run_isaac_h1_maze_real.ps1"
