@echo off
setlocal

REM One-click launcher for gil_controls in ROS2 mode.
REM Requires a Python environment where rclpy + ROS2 message packages are installed.

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%run_gil_controls_ros2.ps1

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*

endlocal





