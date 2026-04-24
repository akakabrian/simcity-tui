"""Micro-benchmark for the hot paths that affect perceived latency.

    .venv/bin/python -m tests.perf
"""

from __future__ import annotations

import asyncio
import statistics
import time

from micropolis_tui.app import SimCityApp
from micropolis_tui.engine import WORLD_H, WORLD_W


def timed(label: str, fn, iterations: int = 50) -> float:
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)  # ms
    # Trim worst outlier to smooth JIT / cache warm-up noise.
    samples.sort()
    trimmed = samples[: max(1, len(samples) - 2)]
    mean = statistics.mean(trimmed)
    p95 = samples[int(len(samples) * 0.95)]
    print(f"  {label:40s} mean={mean:6.2f}ms  p95={p95:6.2f}ms  (n={iterations})")
    return mean


async def main() -> None:
    app = SimCityApp()
    async with app.run_test(size=(180, 60)) as pilot:
        await pilot.pause()
        sim = app.sim
        mv = app.map_view
        print(f"\nbaseline on a {WORLD_W}×{WORLD_H} map, size=(180,60)")
        print()

        # Row-level render: mean over all 100 rows.
        def render_all_rows():
            for y in range(WORLD_H):
                mv.render_line(y)
        timed("render_line × 100 rows (full map)", render_all_rows)

        def render_viewport():
            # ~40 visible rows in a typical viewport.
            for y in range(40):
                mv.render_line(y)
        timed("render_line × 40 rows (viewport)", render_viewport)

        timed("sim.simTick()", sim.simTick)
        timed("sim.getTile loop (12,000 calls)",
              lambda: [sim.getTile(x, y) for y in range(WORLD_H) for x in range(WORLD_W)])

        # Cursor-move end-to-end: watch_cursor_x refreshes only the current row.
        def move_cursor():
            mv.cursor_x = (mv.cursor_x + 1) % WORLD_W
        timed("cursor move (watch + partial refresh)", move_cursor)

        print()


if __name__ == "__main__":
    asyncio.run(main())
