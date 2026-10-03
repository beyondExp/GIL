from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await session.call_tool(name, args)
    txt = res.content[0].text if res.content else "{}"
    try:
        out = json.loads(txt)
        return out if isinstance(out, dict) else {"value": out}
    except Exception:
        return {"_raw": txt}


def _write_data_url(path: Path, data_url: str) -> None:
    if not isinstance(data_url, str) or "base64," not in data_url:
        raise ValueError("not a base64 data url")
    b64 = data_url.split("base64,", 1)[1]
    raw = base64.b64decode(b64.encode("ascii"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", type=str, default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--out", type=str, default="gil_controls/logs/latest_fpv.jpg")
    ap.add_argument("--which", type=str, default="fpv", help="fpv|wide|left|right|extra")
    ap.add_argument("--cam_path", type=str, default="", help="When --which extra: camera prim path key in `images{}`.")
    args = ap.parse_args()

    out = Path(str(args.out))

    async with streamablehttp_client(str(args.url), timeout=60, sse_read_timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            img = await _call(session, "get_latest_image_for", {"robot_kind": "humanoid"})

    which = str(args.which or "fpv").lower().strip()
    if which == "wide":
        data = img.get("image_wide") or ""
    elif which == "left":
        data = img.get("image_left") or ""
    elif which == "right":
        data = img.get("image_right") or ""
    elif which == "extra":
        cam_path = str(args.cam_path or "").strip()
        imgs = img.get("images") or {}
        data = ""
        if cam_path and isinstance(imgs, dict):
            data = imgs.get(cam_path) or ""
    else:
        data = img.get("image") or ""
        if not data:
            data = img.get("image_left") or img.get("image_wide") or ""
    if not data:
        raise SystemExit(f"No image fields present in response keys={sorted(img.keys())}")

    _write_data_url(out, str(data))
    print(json.dumps({"ok": True, "wrote": str(out.resolve()), "keys": sorted(img.keys())}, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

