param(
  [string]$CmdVelTopic = "/cmd_vel",
  [string]$OdomTopic = "/odom",
  [string]$JointStatesTopic = "/joint_states",
  [string]$ImuTopic = "/imu/data",
  [string]$BatteryTopic = "/battery_state",
  [string]$FaultTopic = "/robot_faults",
  [string]$EstopTopic = "/robot_estop",
  [string]$ModeStateTopic = "/humanoid/mode_state",
  [string]$ImageLeftTopic = "/camera/left/image_raw",
  [string]$ImageRightTopic = "/camera/right/image_raw",
  [string]$CameraInfoLeftTopic = "/camera/left/camera_info",
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

$env:GIL_USE_ROS2 = "1"
$env:GIL_HUMANOID_BACKEND = "h1_hardware"
$env:GIL_ROS2_CMD_VEL_TOPIC = $CmdVelTopic
$env:GIL_ROS2_ODOM_TOPIC = $OdomTopic
$env:GIL_ROS2_JOINT_STATES_TOPIC = $JointStatesTopic
$env:GIL_ROS2_IMU_TOPIC = $ImuTopic
$env:GIL_ROS2_BATTERY_TOPIC = $BatteryTopic
$env:GIL_ROS2_FAULT_TOPIC = $FaultTopic
$env:GIL_ROS2_ESTOP_TOPIC = $EstopTopic
$env:GIL_ROS2_MODE_STATE_TOPIC = $ModeStateTopic
$env:GIL_ROS2_IMAGE_LEFT_TOPIC = $ImageLeftTopic
$env:GIL_ROS2_IMAGE_RIGHT_TOPIC = $ImageRightTopic
$env:GIL_ROS2_CAMERA_INFO_LEFT_TOPIC = $CameraInfoLeftTopic
$env:GIL_REQUIRE_MOTION_ENABLE = "1"
$env:GIL_MAX_VX = "0.4"
$env:GIL_MAX_VY = "0.25"
$env:GIL_MAX_WZ = "0.6"

Write-Host "[GIL] Starting H1 hardware bringup profile"
Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] Backend:  $env:GIL_HUMANOID_BACKEND"
Write-Host "[GIL] Topics:   cmd_vel=$CmdVelTopic odom=$OdomTopic joint_states=$JointStatesTopic"

Push-Location $controlsDir
try {
  $conda = Get-Command conda -ErrorAction SilentlyContinue
  if (-not $conda) {
    throw "conda not found. Install Anaconda/Miniconda or adjust this script to use your ROS2 Python."
  }
  & conda run -n $CondaEnv python $mainPy
} finally {
  Pop-Location
}
