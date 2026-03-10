param(
  [string]$CmdVelTopic = "/cmd_vel",
  [string]$OdomTopic = "/odom",
  [string]$ImageLeftTopic = "/camera/left/image_raw",
  [string]$ImageRightTopic = "/camera/right/image_raw",
  [string]$CameraInfoLeftTopic = "/camera/left/camera_info",
  [string]$WalkerModeTopic = "/humanoid/mode",
  [string]$CondaEnv = "gil_ros2"
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
  $here = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $here "..")).Path
}

$repoRoot = Resolve-RepoRoot
$controlsDir = Join-Path $repoRoot "gil_controls"
$mainPy = Join-Path $controlsDir "src\\main.py"

if (-not (Test-Path $mainPy)) {
  throw "Could not find gil_controls entrypoint: $mainPy"
}

# Enable ROS2 backend (publishes to topics using rclpy).
$env:GIL_USE_ROS2 = "1"
$env:GIL_ROS2_CMD_VEL_TOPIC = $CmdVelTopic
$env:GIL_ROS2_ODOM_TOPIC = $OdomTopic
$env:GIL_ROS2_IMAGE_LEFT_TOPIC = $ImageLeftTopic
$env:GIL_ROS2_IMAGE_RIGHT_TOPIC = $ImageRightTopic
$env:GIL_ROS2_CAMERA_INFO_LEFT_TOPIC = $CameraInfoLeftTopic
$env:GIL_ROS2_WALKER_MODE_TOPIC = $WalkerModeTopic

Write-Host "[GIL] Starting gil_controls in ROS2 mode"
Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] Topics: cmd_vel=$CmdVelTopic odom=$OdomTopic"

Push-Location $controlsDir
try {
  # Run inside a ROS2-capable Python environment (rclpy must be importable).
  # We default to the conda env 'gil_ros2' (robostack/ROS2 Humble) but allow override.
  $conda = Get-Command conda -ErrorAction SilentlyContinue
  if (-not $conda) {
    throw "conda not found. Install Anaconda/Miniconda or adjust this script to use your ROS2 Python."
  }

  & conda run -n $CondaEnv python $mainPy
} finally {
  Pop-Location
}


