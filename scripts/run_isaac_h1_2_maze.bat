@echo off
setlocal

REM One-click launcher for Isaac Sim + GIL extensions (H1-2 maze).
REM Optional: set ISAACSIM_PATH to your Isaac Sim install folder.

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%run_isaac_h1_2_maze.ps1

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*

endlocal





