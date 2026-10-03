param(
  [ValidateSet("external","goal")]
  [string]$WalkerMode = "external",

  [switch]$AutoPlay = $true,

  [string]$IsaacSimPath = $env:ISAACSIM_PATH
)

$ErrorActionPreference = "Stop"

$maze = Join-Path $PSScriptRoot "run_isaac_h1_maze_real.ps1"
& $maze -HumanoidVariant h1_2 -UseLabRobot -CameraCapture -CameraCollage -KillExisting -WalkerMode $WalkerMode -AutoPlay:$AutoPlay -IsaacSimPath $IsaacSimPath
exit $LASTEXITCODE
