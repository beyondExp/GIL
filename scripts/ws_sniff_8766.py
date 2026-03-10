import asyncio
import json
import time

import websockets


async def main() -> None:
    uri = "ws://127.0.0.1:8766"
    print(f"[SNIFF] Connecting to {uri} ...", flush=True)
    async with websockets.connect(uri) as ws:
        # Tag this client as humanoid so it receives humanoid-routed commands.
        hello = {"type": "hello", "kind": "humanoid", "robot_kind": "humanoid"}
        await ws.send(json.dumps(hello))
        print(f"[SNIFF] Sent hello: {hello}", flush=True)

        t_end = time.time() + 30.0
        while time.time() < t_end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                print("[SNIFF] (tick) no message yet", flush=True)
                continue
            except Exception as e:
                print(f"[SNIFF] recv error: {e!r}", flush=True)
                break

            try:
                data = json.loads(msg)
            except Exception:
                data = msg
            print(f"[SNIFF] rx: {data}", flush=True)

    print("[SNIFF] Done.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())


