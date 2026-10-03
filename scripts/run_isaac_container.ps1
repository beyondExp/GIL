param(
    [ValidateSet("play-maze", "play-locomotion", "livestream", "bash")]
    [string]$Mode = "play-maze",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not $env:ISAAC_DOCKER_CACHE) {
    $env:ISAAC_DOCKER_CACHE = Join-Path $repo ".cache\isaac-docker"
}
New-Item -ItemType Directory -Force -Path $env:ISAAC_DOCKER_CACHE | Out-Null

$compose = @("docker", "compose", "-f", "docker-compose.isaac.yml")
if ($Build) {
    & @compose build isaac-sim
}

$wslIp = (wsl -d Ubuntu -- hostname -I 2>$null | Select-Object -First 1)
if ($wslIp) { $wslIp = $wslIp.ToString().Trim().Split(" ")[0] }

Write-Host "[GIL] Isaac Sim container mode=$Mode"
Write-Host "[GIL] WebRTC client: connect to 127.0.0.1 (Docker Desktop) or WSL2 IP $wslIp"
Write-Host "[GIL] Ports: TCP 49100 (signaling), UDP 47998 (media)"

$env:ISAAC_MODE = $Mode
& @compose up isaac-sim gil-controls
