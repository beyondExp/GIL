param(
  [ValidateSet("base","full")]
  [string]$Kit = "full",

  [ValidateSet("external","goal")]
  [string]$WalkerMode = "external",

  [ValidateSet("h1","h1_2","g1")]
  [string]$HumanoidVariant = "h1",

  # Replicator multi-camera capture (can slow startup / cause stalls on some rigs).
  [switch]$CaptureAll = $false,

  # Low-res FPV + extra cams + labeled collage for the agent (avoids full-res CaptureAll freeze).
  [switch]$CameraCollage = $false,

  # Enable Replicator camera capture (FPV/wide). Disabling can help when viewport goes black.
  [switch]$CameraCapture = $false,

  # Force policy-driven locomotion (real gait) instead of fallback base-velocity.
  # This may require using the H1 USD even when HumanoidVariant is h1_2.
  [switch]$ForcePolicy = $false,

  # Export a WorldSculpt-compatible GT scene (RGB + per-instance masks + transforms.json).
  [switch]$GtScene = $false,
  [string]$GtOutDir = "",
  [string]$GtRunId = "",
  [int]$GtMaxFrames = 40,
  [double]$GtHz = 2.0,
  [int]$GtMaxInstances = 24,
  [int]$GtMinPixels = 250,

  # Demo: load a converted/reconstructed environment artifact (pointcloud) into the stage.
  [switch]$EnvConverted = $false,
  [string]$EnvConvertedPointcloudNpz = "",
  [double]$EnvConvertedPointSize = 0.08,
  [double]$EnvConvertedRotateXDeg = 0.0,
  [double]$EnvConvertedRotateYDeg = 0.0,
  [double]$EnvConvertedRotateZDeg = 0.0,
  [switch]$EnvConvertedAutoAlign = $true,

  # Demo: load a real environment USD/GLB (absolute path or assets-root-relative like "/Isaac/Environments/...").
  [string]$EnvUsd = "",
  [ValidateSet("","grid","simple_room","warehouse","office","hospital")]
  [string]$EnvPreset = "",

  # Load the full Unitree H1-2 robot USD from the IsaacLab-style asset pack in `_third_party/unitree_sim_isaaclab`
  # (includes the sensor link hierarchy). This avoids synthesizing `camera_link/*`.
  #
  # Note: This is not guaranteed compatible with Isaac's built-in H1 locomotion policy, so you may need
  # fallback locomotion or an IsaacLab policy for H1-2.
  [switch]$UseLabRobot = $false,

  [int]$MazeSeed = 0,
  [int]$MazeWidth = 9,
  [int]$MazeHeight = 9,
  [string]$MazeEnabled = "true",

  # Wider corridors + safer interior spawn defaults
  [double]$MazeCellSize = 1.4,
  [double]$MazeWallThickness = 0.05,
  [double]$MazeWallHeight = 1.6,
  [int]$MazeSpawnCellX = 1,
  [int]$MazeSpawnCellY = 1,

  [switch]$AutoPlay = $true,

  [switch]$KillExisting = $true,

  [string]$SensorsConfig = "",

  [string]$IsaacSimPath = $env:ISAACSIM_PATH
)

$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path -Parent $PSCommandPath) "_isaac_sim.ps1")

function Resolve-RepoRoot {
  $here = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $here "..")).Path
}

$repoRoot = Resolve-RepoRoot
$launcher = Resolve-IsaacSimLauncher $IsaacSimPath
$isaacRoot = $launcher.Root
$env:ISAACSIM_PATH = $isaacRoot
if (-not $env:OMNI_USER_CACHE) { $env:OMNI_USER_CACHE = "E:\GIL\isaacsim-cache" }
$env:OMNI_KIT_ACCEPT_EULA = "YES"

if ($KillExisting) {
  Stop-ExistingIsaacSim -Force
}

$extFolder = Join-Path $repoRoot "robot_simulator\isaac_exts"
if (-not (Test-Path $extFolder)) {
  throw "Extension folder not found: $extFolder"
}

# Select Kit experience config (base starts much faster than full).
$kitConfig = Join-Path $isaacRoot ("Lib\\site-packages\\isaacsim\\apps\\isaacsim.exp.{0}.kit" -f $Kit)
if (-not (Test-Path $kitConfig)) {
  throw "Kit config not found: $kitConfig"
}

# IMPORTANT: Do NOT set GIL_PREFER_H1_2 / GIL_HUMANOID_USD_REL here.
# We want the Isaac-shipped Unitree H1 USD so the built-in H1 locomotion policy works.

$autoPlayVal = if ($AutoPlay) { "true" } else { "false" }

$isH12 = ($HumanoidVariant -eq "h1_2")
$isG1 = ($HumanoidVariant -eq "g1")
$preferH1Policy = if ($isH12 -or $isG1) { "false" } else { "true" }
$requirePolicy = if ($isH12 -or $isG1) { "false" } else { "true" }
$forceH1Policy = if ($isH12 -or $isG1) { "false" } else { "true" }
$captureAllVal = if ($CaptureAll -or $CameraCollage) { "true" } else { "false" }
$cameraCaptureVal = if ($CaptureAll -or $CameraCapture -or $CameraCollage) { "true" } else { "false" }
$collageVal = if ($CameraCollage -or $CaptureAll) { "true" } else { "false" }
$ensureSuiteVal = if ($isH12 -and ($CaptureAll -or $CameraCollage)) { "true" } else { "false" }
$wideEnabledVal = if ($CameraCollage -or $CaptureAll) { "true" } else { "false" }
# Collage at 160x90 is too low-res to see the robot/walls. Use 320x180 (still light-weight).
$camW = if ($CameraCollage) { 320 } else { 320 }
$camH = if ($CameraCollage) { 180 } else { 180 }
$sensorsEnableVal = if ($CameraCollage -or $CaptureAll) { "true" } else { "false" }
$preferH12Val = if ($isH12) { "true" } else { "false" }
$preferG1Val = if ($isG1) { "true" } else { "false" }
$allowCreateParentsVal = if ($isH12) { "true" } else { "false" }
$labFpvPrim = "/World/Humanoid/camera_link/PerspectiveCamera_robot"
$tpvPrim = if ($isH12) { $labFpvPrim } else { "/World/Humanoid/ChaseCamera" }
$fallbackBaseVel = if ($isH12 -or $isG1) { "true" } else { "false" }
$fallbackGait = if ($isH12 -or $isG1) { "true" } else { "false" }
# Kinematic root integration ignores gravity (0G skating). Keep off unless H1-2 needs it.
$allowStallKinematic = if ($isH12) { "true" } else { "false" }

if ($ForcePolicy -and -not $isG1) {
  # Select the known-good H1 USD and require the locomotion policy path.
  $preferH1Policy = "true"
  $requirePolicy = "true"
  $forceH1Policy = "true"
  # IMPORTANT: the H1 policy only matches the H1 robot state layout. Keep H1-2 *sensors*,
  # but set the humanoid variant to H1 so the policy init doesn't shape-mismatch.
  $preferH12Val = "false"
  $fallbackBaseVel = "false"
  $fallbackGait = "false"
  $allowStallKinematic = "false"
}

$variantSetting = $HumanoidVariant
if ($ForcePolicy -and -not $isG1) {
  $variantSetting = "h1"
}

$labRobotUsd = Join-Path $repoRoot "_third_party\\unitree_sim_isaaclab\\assets\\robots\\h1_2-26dof-inspire-base-fix-usd\\h1_2_26dof_with_inspire_rev_1_0.usd"
$useLabRobotVal = if ($UseLabRobot -and (Test-Path $labRobotUsd)) { "true" } else { "false" }
$fpvPrim = if ($useLabRobotVal -eq "true") { $labFpvPrim } else { "/World/Humanoid/FPVCamera" }

$mazeEnabledBool = $true
try {
  $mz = ("" + $MazeEnabled).Trim().ToLower()
  if ($mz -in @("0","false","no","off","disabled","")) { $mazeEnabledBool = $false }
} catch {
  $mazeEnabledBool = $true
}

$args = @(
  $kitConfig,
  "--ext-folder", $extFolder,
  "--enable", "gil.unitree_h1_scene",
  "--enable", "gil.h1_maze_walker",

  # Maze (optional; can be disabled when demoing imported/converted environments).
  ("--/gil/maze/enabled={0}" -f ($(if ($mazeEnabledBool) { "true" } else { "false" }))),
  "--/gil/maze/seed=$MazeSeed",
  "--/gil/maze/width=$MazeWidth",
  "--/gil/maze/height=$MazeHeight",
  "--/gil/maze/cell_size=$MazeCellSize",
  "--/gil/maze/wall_thickness=$MazeWallThickness",
  "--/gil/maze/wall_height=$MazeWallHeight",
  "--/gil/maze/spawn_cell_x=$MazeSpawnCellX",
  "--/gil/maze/spawn_cell_y=$MazeSpawnCellY",

  # Camera: use repo "original" sensor profile unless overridden.
  "--/gil/camera/follow_enabled=false",
  "--/gil/camera/capture_enabled=$cameraCaptureVal",
  "--/gil/camera/capture_all=$captureAllVal",
  "--/gil/camera/collage_enabled=$collageVal",
  "--/gil/camera/ensure_robot_suite=$ensureSuiteVal",
  "--/gil/camera/wide_enabled=$wideEnabledVal",
  "--/gil/camera/fpv_width=$camW",
  "--/gil/camera/fpv_height=$camH",
  "--/gil/camera/wide_width=$camW",
  "--/gil/camera/wide_height=$camH",
  "--/gil/sensors/enable=$sensorsEnableVal",
  # Wide/TPV should be a stable third-person camera.
  "--/gil/camera/tpv_prim_path=/World/Humanoid/ChaseCamera",
  # FPV should use the robot's forward camera (realistic sensor layout).
  # When using the IsaacLab-style robot asset, prefer its camera_link camera prim.
  "--/gil/camera/fpv_prim_path=$fpvPrim",
  # If we want the original Unitree sensor layout (H1-2), allow creating the camera parent prim chain.
  "--/gil/camera/allow_create_camera_parents=$allowCreateParentsVal",
  # FPV tuning: ensure the camera looks forward down the corridor (small upward pitch).
  "--/gil/camera/fpv_x=0.25",
  "--/gil/camera/fpv_y=0.00",
  # IMPORTANT: fpv_z is an offset added to the robot root z (which is ~1.05m for H1).
  # Use a small offset so camera height is ~1.2–1.3m (below wall tops) instead of ~2.0m.
  "--/gil/camera/fpv_z=0.20",
  # Slight downward pitch so FPV sees near walls/corridor (but not just floor).
  "--/gil/camera/fpv_pitch_offset_deg=-10",
  "--/gil/camera/fpv_yaw_offset_deg=0",
  "--/gil/camera/fpv_roll_offset_deg=0",

  # Optional: GT scene export (WorldSculpt-style). (Only add the optional string args when set.)
  ("--/gil/gt_scene/enabled={0}" -f ($(if ($GtScene) { "true" } else { "false" }))),
  ("--/gil/gt_scene/max_frames={0}" -f $GtMaxFrames),
  ("--/gil/gt_scene/sample_hz={0}" -f $GtHz),
  ("--/gil/gt_scene/max_instances={0}" -f $GtMaxInstances),
  ("--/gil/gt_scene/min_pixels={0}" -f $GtMinPixels),

  # Optional: converted env visualization (pointcloud).
  ("--/gil/env_converted/enabled={0}" -f ($(if ($EnvConverted) { "true" } else { "false" }))),
  ("--/gil/env_converted/point_size={0}" -f $EnvConvertedPointSize),
  ("--/gil/env_converted/auto_align_to_env={0}" -f ($(if ($EnvConvertedAutoAlign) { "true" } else { "false" }))),
  ("--/gil/env_converted/rotate_x_deg={0}" -f $EnvConvertedRotateXDeg),
  ("--/gil/env_converted/rotate_y_deg={0}" -f $EnvConvertedRotateYDeg),
  ("--/gil/env_converted/rotate_z_deg={0}" -f $EnvConvertedRotateZDeg),

  # Force the scene spawner to pick H1 from Isaac assets root (policy-compatible).
  "--/gil/humanoid/prefer_h1_policy=$preferH1Policy",
  "--/gil/humanoid/prefer_h1_2=$preferH12Val",
  "--/gil/humanoid/prefer_g1=$preferG1Val",
  "--/gil/humanoid/variant=$variantSetting",

  # Optional: override robot USD to the IsaacLab-style H1-2 asset pack.
  "--/gil/humanoid/use_lab_robot=$useLabRobotVal",

  "--/gil/walker/mode=$WalkerMode",
  "--/gil/walker/prim_path=/World/Humanoid",

  # Controls bridge
  "--/gil/mcp/ws_uri=ws://127.0.0.1:8766",

  # Tuning for tight mazes: turn first, don't overdrive corners.
  # Conservative speeds reduce corner clipping / falls in goal mode.
  "--/gil/walker/vx=0.14",
  "--/gil/walker/wz_gain=1.0",
  "--/gil/walker/wz_max=0.60",
  "--/gil/walker/turn_in_place_err=0.60",
  "--/gil/walker/turn_min_vx_scale=0.05",
  "--/gil/walker/stop_radius=0.35",

  # Locomotion policy config (H1-2 often doesn't match Isaac's built-in H1 policy).
  "--/gil/walker/require_policy=$requirePolicy",
  "--/gil/walker/force_h1_policy=$forceH1Policy",
  "--/gil/walker/fallback_base_velocity=$fallbackBaseVel",
  "--/gil/walker/fallback_simple_gait=$fallbackGait",
  "--/gil/walker/allow_stall_kinematic_fallback=$allowStallKinematic",

  "--/gil/humanoid/auto_play=$autoPlayVal"
)

if ($GtScene) {
  if ($GtOutDir) {
    $args += ("--/gil/gt_scene/output_dir={0}" -f $GtOutDir)
  }
  if ($GtRunId) {
    $args += ("--/gil/gt_scene/run_id={0}" -f $GtRunId)
  }
}

if ($EnvConverted) {
  if ($EnvConvertedPointcloudNpz) {
    $args += ("--/gil/env_converted/pointcloud_npz={0}" -f $EnvConvertedPointcloudNpz)
  }
}

if ($EnvUsd) {
  $args += ("--/gil/env/usd={0}" -f $EnvUsd)
}
if ($EnvPreset) {
  $args += ("--/gil/env/preset={0}" -f $EnvPreset)
}

if ($useLabRobotVal -eq "true") {
  # Point scene loader at the full H1-2 USD (absolute path).
  $args += @("--/gil/humanoid/usd_rel=$labRobotUsd")
}
if ($isG1) {
  $args += @(
    "--/gil/humanoid/usd_rel=/Isaac/Robots/Unitree/G1_23dof/g1.usd",
    "--/gil/humanoid/spawn_z=0.76",
    "--/gil/camera/fpv_z=0.82"
  )
}

if ($SensorsConfig -and (Test-Path $SensorsConfig)) {
  $args += @("--/gil/robot/sensors_config=$SensorsConfig")
} else {
  $defaultCfg = if ($isH12) {
    Join-Path $repoRoot "robot_simulator\\config\\robots\\unitree_h1_2_sensors.json"
  } elseif ($isG1) {
    Join-Path $repoRoot "robot_simulator\\config\\robots\\unitree_g1_sensors.json"
  } else {
    Join-Path $repoRoot "robot_simulator\\config\\robots\\unitree_h1_sensors.json"
  }
  if (Test-Path $defaultCfg) {
    $args += @("--/gil/robot/sensors_config=$defaultCfg")
  }

# Ensure the synthetic FPVCamera pose is re-applied from sensors_config when using H1.
if (-not $isH12) {
  $args += @("--/gil/camera/force_fpv_pose=true")
}
# Image rotate can be used as a last-resort fix; default to 0 now that FPV camera orient is authored via quaternion.
$args += @("--/gil/camera/fpv_image_rotate_deg=0")
}

# NOTE: We intentionally do NOT pass Kit's `-AutoPlay` here.
# Our walker extension starts the timeline after it has confirmed physics scene presence and after
# World/sim-context init, which avoids triggering SimulationManager warmup too early (the cause of
# "Failed to create simulation view: no active physics scene found").

Write-Host "[GIL] RepoRoot: $repoRoot"
Write-Host "[GIL] IsaacSim:  $isaacRoot ($($launcher.Kind))"
Write-Host "[GIL] Mode:      $WalkerMode  AutoPlay: $AutoPlay"
Write-Host "[GIL] Variant:   $HumanoidVariant"
Write-Host "[GIL] Locomotion: require_policy=$requirePolicy force_h1_policy=$forceH1Policy"

& $launcher.Command @args


