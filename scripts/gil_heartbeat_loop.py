#!/usr/bin/env python3
"""Keep GIL humanoid motion armed (heartbeat timeout defaults to 2s)."""
from __future__ import annotations

import json
import time
import urllib.request

MCP_URL = "http://127.0.0.1:6769/mcp/"


def main() -> None:
    while True:
        req = urllib.request.Request(
            MCP_URL,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "send_humanoid_heartbeat", "arguments": {"source": "loop"}}}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2.0).read()
        except Exception as exc:
            print(f"[heartbeat] {exc!r}", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
