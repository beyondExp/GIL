# Warehouse demo (Isaac Sim + God's Eye View + GIL)

This demo launches:

- `gil_controls` (MCP/REST on `:6769`)
- Isaac Sim with the **warehouse** environment preset
- God's Eye View dev server (`:5173`)
- a `world_anchor` so the robot appears at a stable real-world lon/lat in God's Eye View

## Run (Windows PowerShell)

From the repo root:

```powershell
.\scripts\bringup.ps1 -Demo warehouse
```

To pin the sim to a specific world position:

```powershell
.\scripts\bringup.ps1 -Demo warehouse -Lat 47.440513741568296 -Lon 8.563542482184204 -Yaw 0
```

## Use

- Open God's Eye View: `http://127.0.0.1:5173/`
- Enable **GIL Robot** panel → click **FOCUS**
- Use **RESET** to re-apply the anchor + reset the robot pose

