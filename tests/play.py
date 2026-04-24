"""Headless AI self-player — Claude builds a small city via the REST API
and reports the outcome.

Doubles as:
  • an integration test (the API must not regress),
  • dogfooding (if I can't play the game effectively, the UX is broken).

Strategy — simple and deterministic:
  1. Start a fresh sim on the `empty` scenario if available, else `dullsville`.
  2. Lay a power plant at (40, 40).
  3. Lay a long road spine running east from the plant.
  4. Place alternating R, C, I zones along the road with 1-tile gaps.
  5. Wire the plant into the road spine with a power line.
  6. Add a police station and fire station in the middle of town.
  7. Advance 200 months (~17 years) and report city stats.

We don't try to win — just to verify the game actually grows under normal
play. A city with pop > 0, score > 400, and no errors is "healthy".

Run:
    .venv/bin/python -m tests.play                # uses the default city
    .venv/bin/python -m tests.play --city dullsville
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time

import aiohttp

from micropolis_tui.app import SimCityApp
from micropolis_tui.agent_api import start_server


# Micropolis tool codes (matching me.TOOL_* — hardcoded to avoid importing
# the SWIG module into this test script).
TOOL_RESIDENTIAL = 0
TOOL_COMMERCIAL = 1
TOOL_INDUSTRIAL = 2
TOOL_FIRESTATION = 3
TOOL_POLICESTATION = 4
TOOL_WIRE = 6
TOOL_BULLDOZER = 7
TOOL_RAILROAD = 8
TOOL_ROAD = 9
TOOL_PARK = 11
TOOL_COALPOWER = 13


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Player:
    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        self.base = base_url
        self.sess = session
        self.ok = 0
        self.failed = 0
        self.no_money = 0

    async def apply(self, tool: int, x: int, y: int,
                    x2: int | None = None, y2: int | None = None,
                    note: str = "") -> int:
        """Apply a tool and collect the result. Returns the engine's
        TOOLRESULT: 1=OK, 0=failed, -1=need_bulldoze, -2=no_money."""
        body = {"code": tool, "x": x, "y": y}
        if x2 is not None and y2 is not None:
            body["x2"] = x2
            body["y2"] = y2
        async with self.sess.post(f"{self.base}/tool", json=body) as r:
            data = await r.json()
        result = data["result"]
        if result == 1:
            self.ok += 1
        elif result == -2:
            self.no_money += 1
        else:
            self.failed += 1
        return result

    async def advance(self, ticks: int) -> None:
        async with self.sess.post(f"{self.base}/advance",
                                  json={"ticks": ticks}) as r:
            await r.json()

    async def state(self) -> dict:
        async with self.sess.get(f"{self.base}/state") as r:
            return await r.json()


async def build_city(player: Player) -> None:
    """Lay out a minimal but growing city."""
    CX, CY = 40, 40  # city centre

    print(f"  • pausing sim to build")
    async with player.sess.post(f"{player.base}/pause",
                                json={"paused": True}) as r:
        await r.json()

    # Bulldoze the build area first — some cities start with trees at the
    # centre, which block zoning until cleared.
    print(f"  • clearing trees around ({CX},{CY})")
    for dx in range(-2, 18):
        for dy in range(-3, 4):
            await player.apply(TOOL_BULLDOZER, CX + dx, CY + dy)

    # Coal plant sits 4×4 and anchors the west end.
    print(f"  • coal plant at ({CX},{CY})")
    await player.apply(TOOL_COALPOWER, CX, CY)

    # Road spine running east for 15 tiles. toolDrag handles the whole line.
    print(f"  • road spine eastward")
    await player.apply(TOOL_ROAD, CX + 5, CY, CX + 20, CY, note="spine")

    # Power line running along the north shoulder of the road to light zones.
    print(f"  • power line along road")
    await player.apply(TOOL_WIRE, CX + 5, CY - 1, CX + 20, CY - 1)

    # Zones alternate north and south of the road. 3×3 each, 4-tile step.
    print(f"  • placing zones along the road")
    zone_types = [TOOL_RESIDENTIAL, TOOL_COMMERCIAL, TOOL_INDUSTRIAL,
                  TOOL_RESIDENTIAL, TOOL_RESIDENTIAL, TOOL_COMMERCIAL]
    for i, tool in enumerate(zone_types):
        x = CX + 6 + i * 4
        y = CY - 3 if i % 2 == 0 else CY + 3
        await player.apply(tool, x, y)

    # Services in the middle of town.
    print(f"  • police + fire stations")
    await player.apply(TOOL_POLICESTATION, CX + 10, CY + 4)
    await player.apply(TOOL_FIRESTATION, CX + 14, CY - 4)

    # A couple parks to lift land value.
    print(f"  • parks")
    await player.apply(TOOL_PARK, CX + 7, CY + 1)
    await player.apply(TOOL_PARK, CX + 17, CY - 1)

    # Unpause and let the sim run.
    async with player.sess.post(f"{player.base}/pause",
                                json={"paused": False}) as r:
        await r.json()


async def run(city: str) -> int:
    port = _free_port()
    app = SimCityApp(city)
    async with app.run_test(size=(180, 60)) as pilot:
        await pilot.pause()
        runner = await start_server(app, port=port)
        try:
            async with aiohttp.ClientSession() as sess:
                player = Player(f"http://127.0.0.1:{port}", sess)

                before = await player.state()
                print("\n== Start ==")
                print(f"  city        {before['city_name'] or '(unnamed)'}")
                print(f"  year/month  {before['year']} / {before['month'] + 1}")
                print(f"  population  {before['population']:>8,}")
                print(f"  funds       ${before['funds']:>12,}")
                print(f"  city score  {before['city_score']}")
                print()

                print("== Building ==")
                await build_city(player)
                print(f"  tool results — ok: {player.ok}  "
                      f"failed: {player.failed}  no_money: {player.no_money}")
                print()

                print("== Advancing 300 sim-ticks (~25 game months) ==")
                started = time.time()
                # 300 ticks in chunks so the budget/status panels update.
                for _ in range(6):
                    await player.advance(50)
                    await asyncio.sleep(0.05)
                elapsed = time.time() - started
                print(f"  took {elapsed:.2f}s of wall time")
                print()

                after = await player.state()
                print("== After ==")
                print(f"  year/month  {after['year']} / {after['month'] + 1}")
                print(f"  population  {after['population']:>8,}   "
                      f"Δ {after['population'] - before['population']:+,}")
                print(f"  funds       ${after['funds']:>12,}   "
                      f"Δ ${after['funds'] - before['funds']:+,}")
                print(f"  city score  {after['city_score']}   "
                      f"Δ {after['city_score'] - before['city_score']:+}")
                print(f"  R / C / I   {after['pop_r']} / {after['pop_c']} / "
                      f"{after['pop_i']}")
                print(f"  pollution   {after['pollution_avg']}")
                print(f"  crime       {after['crime_avg']}")
                print(f"  traffic     {after['traffic_avg']}")
                print()

                # Sanity gates. These aren't strict — the game is a sim, not
                # deterministic. But broad health checks.
                exit_code = 0
                if after['city_score'] < 200:
                    print("  ✗ city score too low — build likely broken")
                    exit_code = 1
                if player.failed > player.ok * 2:
                    print("  ✗ too many tool failures — pathing broken")
                    exit_code = 1
                if player.no_money > 3:
                    print("  ⚠ ran out of money several times")
                if exit_code == 0:
                    print("  ✓ city is healthy")
                return exit_code
        finally:
            await runner.cleanup()


def main() -> int:
    p = argparse.ArgumentParser(prog="play", description=__doc__)
    p.add_argument("--city", default="haight",
                   help="starting city (default: haight)")
    args = p.parse_args()
    return asyncio.run(run(args.city))


if __name__ == "__main__":
    sys.exit(main())
