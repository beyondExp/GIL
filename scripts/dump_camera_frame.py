#!/usr/bin/env python3
"""Dump a single camera frame from gil_controls MCP to disk.

Usage:
  python scripts/dump_camera_frame.py humanoid
  python scripts/dump_camera_frame.py arm
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


MCP_URL = os.getenv("GIL_CONTROLS_URL", "http://127.0.0.1:6769/mcp/")


def _decode_data_url(data_url: str) -> bytes:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        raise ValueError("not a data:image/... data URL")
    try:
        header, b64 = data_url.split(",", 1)
    except ValueError as exc:
        raise ValueError("invalid data URL") from exc
    return base64.b64decode(b64)


async def _call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await session.call_tool(tool, args or {})
    text = res.content[0].text if res.content else "{}"
    return json.loads(text)


async def main() -> int:
    kind = (sys.argv[1] if len(sys.argv) > 1 else "humanoid").strip().lower()
    if kind not in ("humanoid", "arm"):
        print("robot_kind must be 'humanoid' or 'arm'")
        return 2

    out_dir = Path("assets") / "camera_dumps"
    out_dir.mkdir(parents=True, exist_ok=True)

    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)
            img = await _call(session, "get_latest_image_for", {"robot_kind": kind})
            if img.get("error"):
                print(json.dumps({"ok": False, "error": img["error"]}, indent=2))
                return 1
            images = img.get("images") or {}
            if isinstance(images, dict) and images:
                for k, v in list(images.items()):
                    if isinstance(v, str) and v.startswith("data:image/"):
                        try:
                            raw_k = _decode_data_url(v)
                            (out_dir / f"{kind}_{k}.jpg").write_bytes(raw_k)
                        except Exception:
                            pass

            data_url = img.get("image") or ""
            if not data_url:
                print(json.dumps({"ok": False, "error": "missing image field"}, indent=2))
                return 1

            raw = _decode_data_url(data_url)
            out_path = out_dir / f"{kind}_latest.jpg"
            out_path.write_bytes(raw)

            # Also dump wide/left/right if present (best-effort).
            for extra_key in ("image_wide", "image_left", "image_right"):
                v = img.get(extra_key)
                if isinstance(v, str) and v.startswith("data:image/"):
                    try:
                        (out_dir / f"{kind}_{extra_key}.jpg").write_bytes(_decode_data_url(v))
                    except Exception:
                        pass

            cam_info = img.get("camera_info") or {}
            print(
                json.dumps(
                    {
                        "ok": True,
                        "robot_kind": kind,
                        "saved": str(out_path).replace("\\", "/"),
                        "camera_info": cam_info,
                        "keys": sorted(img.keys()),
                        "images_keys": sorted(list(images.keys())) if isinstance(images, dict) else [],
                    },
                    indent=2,
                )
            )
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

