$ErrorActionPreference = "Stop"

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}
"@

$p = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "Isaac Sim*" } | Select-Object -First 1
if (-not $p) { Write-Output "no_isaac_window"; exit 0 }

# 9 = SW_RESTORE
[Win32]::ShowWindowAsync($p.MainWindowHandle, 9) | Out-Null
[Win32]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Write-Output ("focused_pid={0}" -f $p.Id)

