"""QA for the agent HTTP API. Launches a sim + server on a free port,
hits each endpoint, asserts response shape. Run with `python -m tests.api_qa`."""

from __future__ import annotations

import asyncio
import json
import socket
import sys

import aiohttp

from micropolis_tui.app import SimCityApp
from micropolis_tui.agent_api import start_server


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


async def main() -> int:
    port = _free_port()
    app = SimCityApp()
    # Run the app as a Textual test so on_mount wires up widgets + history.
    async with app.run_test(size=(180, 60)) as pilot:
        await pilot.pause()
        runner = await start_server(app, port=port)
        try:
            base = f"http://127.0.0.1:{port}"
            async with aiohttp.ClientSession() as sess:
                # /
                async with sess.get(f"{base}/") as r:
                    data = await r.json()
                    await _assert(r.status == 200, f"/ status={r.status}")
                    await _assert("endpoints" in data, "root missing endpoints")
                print(f"  \033[32m✓\033[0m GET /")

                # /state
                async with sess.get(f"{base}/state") as r:
                    s = await r.json()
                    for k in ("city_name", "year", "population", "funds",
                              "cursor", "overlay_mode"):
                        await _assert(k in s, f"state missing {k}")
                print(f"  \033[32m✓\033[0m GET /state")

                # /tools
                async with sess.get(f"{base}/tools") as r:
                    tools = await r.json()
                    await _assert(len(tools) >= 9, f"expected ≥9 tools got {len(tools)}")
                    labels = {t["label"] for t in tools}
                    await _assert("Railroad" in labels, f"missing Railroad in {labels}")
                print(f"  \033[32m✓\033[0m GET /tools")

                # /tile
                async with sess.get(f"{base}/tile?x=60&y=50") as r:
                    t = await r.json()
                    await _assert(t["x"] == 60 and t["y"] == 50, t)
                print(f"  \033[32m✓\033[0m GET /tile")

                # /tile (out of bounds)
                async with sess.get(f"{base}/tile?x=500&y=500") as r:
                    await _assert(r.status == 400, f"oob status={r.status}")
                print(f"  \033[32m✓\033[0m GET /tile (oob rejected)")

                # /map (default fmt=cls)
                async with sess.get(f"{base}/map") as r:
                    m = await r.json()
                    await _assert(len(m["grid"]) == 100, len(m["grid"]))
                    await _assert(len(m["grid"][0]) == 120, len(m["grid"][0]))
                print(f"  \033[32m✓\033[0m GET /map")

                # /overlays/<name>
                async with sess.get(f"{base}/overlays/crime") as r:
                    o = await r.json()
                    await _assert(o["w"] == 15 and o["h"] == 12, o)
                print(f"  \033[32m✓\033[0m GET /overlays/crime")

                # POST /tool — apply road at a known dirt spot
                # Find a dirt tile first
                dirt = None
                for y in range(100):
                    for x in range(120):
                        if app.map_view._map[x * 100 + y] & 0x3FF == 0:
                            dirt = (x, y)
                            break
                    if dirt:
                        break
                assert dirt is not None
                funds_before = app.sim.totalFunds
                async with sess.post(f"{base}/tool",
                                     json={"code": 9, "x": dirt[0], "y": dirt[1]}) as r:
                    res = await r.json()
                    await _assert(res["result"] == 1, res)  # TOOLRESULT_OK
                await _assert(app.sim.totalFunds < funds_before,
                              f"funds: {funds_before} → {app.sim.totalFunds}")
                print(f"  \033[32m✓\033[0m POST /tool (road)")

                # POST /advance
                year_before = app.sim.cityYear
                month_before = app.sim.cityMonth
                async with sess.post(f"{base}/advance",
                                     json={"ticks": 50}) as r:
                    adv = await r.json()
                    await _assert(adv["ticks"] == 50, adv)
                print(f"  \033[32m✓\033[0m POST /advance")

                # POST /pause
                async with sess.post(f"{base}/pause",
                                     json={"paused": True}) as r:
                    p = await r.json()
                    await _assert(p["paused"] is True, p)
                await _assert(app.paused is True, "app.paused should be True")
                print(f"  \033[32m✓\033[0m POST /pause")

                # POST /overlay
                async with sess.post(f"{base}/overlay",
                                     json={"mode": "pollution"}) as r:
                    o = await r.json()
                    await _assert(o["mode"] == "pollution", o)
                await _assert(app.map_view.overlay_mode == "pollution",
                              app.map_view.overlay_mode)
                print(f"  \033[32m✓\033[0m POST /overlay")

                # POST /tax
                async with sess.post(f"{base}/tax", json={"rate": 11}) as r:
                    t = await r.json()
                    await _assert(t["rate"] == 11, t)
                await _assert(app.sim.cityTax == 11, app.sim.cityTax)
                print(f"  \033[32m✓\033[0m POST /tax")

                # POST /tool with bad body
                async with sess.post(f"{base}/tool", data="not json") as r:
                    await _assert(r.status == 400, r.status)
                print(f"  \033[32m✓\033[0m POST /tool (bad body rejected)")

            print("\n  \033[32m13/13 agent-API scenarios passed\033[0m")
            return 0
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
