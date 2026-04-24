"""HTTP REST API that exposes the live sim so AI agents can observe and
control the city. Runs alongside the Textual UI (or standalone in headless
mode) on localhost.

Schema loosely modelled on andrewedunn/hallucinating-splines — we use simple
JSON POSTs for actions instead of a wire-level protocol.

Endpoints:
    GET  /                  — server info + endpoint list
    GET  /state             — live city state (pop, funds, year, averages, etc.)
    GET  /tools             — tool catalogue
    GET  /map?fmt=ids|cls   — full 120×100 tile grid (default fmt=cls)
    GET  /tile?x=&y=        — single tile info
    GET  /overlays/{name}   — downsampled 15×12 density map
    GET  /history           — monthly stats history
    POST /tool              — apply a tool: {code, x, y, [x2, y2]}
    POST /advance           — tick N times while paused: {ticks: int}
    POST /pause             — {paused: bool}
    POST /overlay           — {mode: str}
    POST /tax               — {rate: int}   (0..20)
    GET  /events            — server-sent events stream of state snapshots

All coordinates are in tile space (0..119 × 0..99). Errors return JSON
`{error: ...}` with an HTTP status code.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
from typing import Any

from aiohttp import web

import micropolisengine as me  # pyright: ignore[reportMissingImports]

from . import tiles
from .engine import WORLD_H, WORLD_W
from .screens import OVERLAY_MODES


def state_snapshot(app) -> dict[str, Any]:
    s = app.sim
    mv = app.map_view
    return {
        "city_name": s.cityName or "",
        "year": s.cityYear,
        "month": s.cityMonth,
        "population": max(s.cityPop, 0),
        "pop_r": s.resPop, "pop_c": s.comPop, "pop_i": s.indPop,
        "funds": s.totalFunds,
        "cash_flow": s.cashFlow,
        "tax_rate": s.cityTax,
        "fund_road_pct": round(s.roadPercent * 100),
        "fund_police_pct": round(s.policePercent * 100),
        "fund_fire_pct": round(s.firePercent * 100),
        "traffic_avg": s.trafficAverage,
        "pollution_avg": s.pollutionAverage,
        "crime_avg": s.crimeAverage,
        "city_score": s.cityScore,
        "city_class": s.cityClass,
        "map_serial": s.mapSerial,
        "paused": app.paused,
        "cursor": {"x": mv.cursor_x, "y": mv.cursor_y},
        "overlay_mode": mv.overlay_mode,
        "world": {"w": WORLD_W, "h": WORLD_H},
    }


def _tile_info(app, x: int, y: int) -> dict[str, Any]:
    app.map_view._bind_map_buffer()
    tid = app.map_view._map[x * WORLD_H + y] & tiles.TILE_MASK
    glyph, klass = tiles._TABLE[tid]
    return {"x": x, "y": y, "id": tid, "glyph": glyph, "class": klass}


def build_app(game_app) -> web.Application:
    """Wrap the live Textual app in an aiohttp web.Application."""
    routes = web.RouteTableDef()

    @routes.get("/")
    async def root(request):
        return web.json_response({
            "name": "micropolis-tui agent API",
            "endpoints": [
                "GET /state", "GET /tools", "GET /map", "GET /tile",
                "GET /overlays/<name>", "GET /history", "GET /events",
                "POST /tool", "POST /advance", "POST /pause",
                "POST /overlay", "POST /tax",
            ],
            "world": {"w": WORLD_W, "h": WORLD_H},
            "overlay_modes": OVERLAY_MODES,
        })

    @routes.get("/state")
    async def state(request):
        return web.json_response(state_snapshot(game_app))

    @routes.get("/tools")
    async def tools(request):
        from .app import TOOLS
        return web.json_response([
            {"key": t.key, "label": t.label, "code": t.code, "cost": t.cost}
            for t in TOOLS
        ])

    @routes.get("/map")
    async def full_map(request):
        fmt = request.query.get("fmt", "cls")
        game_app.map_view._bind_map_buffer()
        m = game_app.map_view._map
        mask = tiles.TILE_MASK
        if fmt == "ids":
            # Row-major 2D list of tile IDs.
            grid = [
                [m[x * WORLD_H + y] & mask for x in range(WORLD_W)]
                for y in range(WORLD_H)
            ]
        else:
            grid = [
                [tiles._TABLE[m[x * WORLD_H + y] & mask][1] for x in range(WORLD_W)]
                for y in range(WORLD_H)
            ]
        return web.json_response({"w": WORLD_W, "h": WORLD_H, "fmt": fmt, "grid": grid})

    @routes.get("/tile")
    async def tile(request):
        try:
            x = int(request.query["x"])
            y = int(request.query["y"])
        except (KeyError, ValueError):
            return web.json_response({"error": "missing/invalid x,y"}, status=400)
        if not (0 <= x < WORLD_W and 0 <= y < WORLD_H):
            return web.json_response({"error": "out of bounds"}, status=400)
        return web.json_response(_tile_info(game_app, x, y))

    @routes.get("/overlays/{name}")
    async def overlay(request):
        name = request.match_info["name"]
        if name not in OVERLAY_MODES or name == "off":
            return web.json_response(
                {"error": f"unknown overlay {name!r}"}, status=400)
        from .screens import overlay_buffer
        buf = overlay_buffer(game_app.sim, name)
        assert buf is not None
        # 15×12 row-major grid.
        grid = [[buf[y * 15 + x] for x in range(15)] for y in range(12)]
        return web.json_response({"name": name, "w": 15, "h": 12, "grid": grid})

    @routes.get("/history")
    async def history(request):
        return web.json_response({"samples": game_app._history})

    @routes.post("/tool")
    async def apply_tool(request):
        try:
            body = await request.json()
            code = int(body["code"])
            x = int(body["x"])
            y = int(body["y"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return web.json_response({"error": "body must be {code, x, y, [x2, y2]}"},
                                     status=400)
        x2 = int(body["x2"]) if "x2" in body else x
        y2 = int(body["y2"]) if "y2" in body else y
        for c in (x, y, x2, y2):
            if not (0 <= c < max(WORLD_W, WORLD_H)):
                return web.json_response({"error": "out of bounds"}, status=400)
        if (x, y) == (x2, y2):
            result = game_app.sim.doTool(code, x, y)
        else:
            result = game_app.sim.toolDrag(code, x, y, x2, y2)
        # Trigger a repaint on the TUI side.
        game_app.map_view.refresh_all_tiles()
        return web.json_response({
            "result": result,
            "result_name": _TOOLRESULT_NAMES.get(result, str(result)),
            "funds": game_app.sim.totalFunds,
        })

    @routes.post("/advance")
    async def advance(request):
        try:
            body = await request.json()
            n = int(body.get("ticks", 1))
        except (ValueError, json.JSONDecodeError):
            return web.json_response({"error": "body must be {ticks: int}"}, status=400)
        n = max(1, min(n, 10_000))
        before_year = game_app.sim.cityYear
        before_month = game_app.sim.cityMonth
        for _ in range(n):
            game_app.sim.simTick()
        return web.json_response({
            "ticks": n,
            "year_before": before_year,
            "month_before": before_month,
            "year_after": game_app.sim.cityYear,
            "month_after": game_app.sim.cityMonth,
        })

    @routes.post("/pause")
    async def pause(request):
        try:
            body = await request.json()
            game_app.paused = bool(body["paused"])
        except (KeyError, json.JSONDecodeError):
            return web.json_response({"error": "body must be {paused: bool}"},
                                     status=400)
        return web.json_response({"paused": game_app.paused})

    @routes.post("/overlay")
    async def overlay_set(request):
        try:
            body = await request.json()
            mode = body["mode"]
        except (KeyError, json.JSONDecodeError):
            return web.json_response({"error": "body must be {mode: str}"},
                                     status=400)
        if mode not in OVERLAY_MODES:
            return web.json_response({"error": f"unknown overlay {mode!r}"},
                                     status=400)
        game_app.map_view.set_overlay_mode(mode)
        return web.json_response({"mode": mode})

    @routes.post("/tax")
    async def tax(request):
        try:
            body = await request.json()
            rate = int(body["rate"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return web.json_response({"error": "body must be {rate: int}"},
                                     status=400)
        rate = max(0, min(20, rate))
        game_app.sim.cityTax = rate
        return web.json_response({"rate": rate})

    @routes.get("/events")
    async def events(request):
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        try:
            while True:
                payload = json.dumps(state_snapshot(game_app))
                await resp.write(f"data: {payload}\n\n".encode())
                await asyncio.sleep(1.0)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp

    aio_app = web.Application()
    aio_app.add_routes(routes)
    return aio_app


_TOOLRESULT_NAMES = {
    me.TOOLRESULT_OK: "ok",
    me.TOOLRESULT_FAILED: "failed",
    me.TOOLRESULT_NEED_BULLDOZE: "need_bulldoze",
    me.TOOLRESULT_NO_MONEY: "no_money",
}


async def start_server(game_app, host: str = "127.0.0.1", port: int = 8787) -> web.AppRunner:
    """Bring up the aiohttp server as a task on the current event loop."""
    aio_app = build_app(game_app)
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
