$ErrorActionPreference = "Stop"

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = "D:\BBotOldANDExperiments\Experiments\GIL\assets\screenshots"
$outPath = Join-Path $outDir ("screen_{0}.png" -f $ts)

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Capture the full virtual desktop (all monitors), not just PrimaryScreen.
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bounds = New-Object System.Drawing.Rectangle($vs.Left, $vs.Top, $vs.Width, $vs.Height)
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()

Write-Output $outPath

