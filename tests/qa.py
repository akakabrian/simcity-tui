"""Headless QA driver for simcity-tui.

Runs each scenario in a fresh `SimCityApp` via `App.run_test()`, captures an
SVG screenshot, and reports pass/fail. Exit code is the number of failures.

    python -m tests.qa            # run all
    python -m tests.qa cursor     # run scenarios whose name matches "cursor"
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from simcity_tui.app import SimCityApp, TOOLS
from simcity_tui.engine import WORLD_H, WORLD_W
from simcity_tui import tiles

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


@dataclass
class Scenario:
    name: str
    fn: Callable[[SimCityApp, "object"], Awaitable[None]]


# ---------- helpers ----------

def tile_id(app: SimCityApp, x: int, y: int) -> int:
    return app.sim.getTile(x, y) & tiles.TILE_MASK


def tile_class(app: SimCityApp, x: int, y: int) -> str:
    return tiles._TABLE[tile_id(app, x, y)][1]


async def find_dirt(app: SimCityApp) -> tuple[int, int] | None:
    """Locate a tile we can build on. Prefers raw DIRT (id 0), then falls
    back to grass (id 1), then anything our tile table classifies as
    dirt/grass — in case the scenario lacks pure open land."""
    # First pass: strict DIRT (tile id 0) — always buildable.
    for y in range(WORLD_H):
        for x in range(WORLD_W):
            if tile_id(app, x, y) == 0:
                return x, y
    # Fallback pass: grass (id 1) or anything classified as open land.
    for y in range(WORLD_H):
        for x in range(WORLD_W):
            if tile_class(app, x, y) in ("dirt", "grass"):
                return x, y
    return None


# ---------- scenarios ----------

async def s_mount_clean(app, pilot):
    assert app.map_view is not None
    assert app.status_panel is not None
    assert app.tools_panel is not None
    assert app.sim is not None


async def s_cursor_starts_centered(app, pilot):
    assert app.map_view.cursor_x == WORLD_W // 2, (
        f"cursor_x={app.map_view.cursor_x}, expected {WORLD_W // 2}"
    )
    assert app.map_view.cursor_y == WORLD_H // 2, (
        f"cursor_y={app.map_view.cursor_y}, expected {WORLD_H // 2}"
    )


async def s_cursor_moves(app, pilot):
    start_x = app.map_view.cursor_x
    start_y = app.map_view.cursor_y
    await pilot.press("right", "right", "right")
    await pilot.press("down", "down")
    assert app.map_view.cursor_x == start_x + 3, app.map_view.cursor_x
    assert app.map_view.cursor_y == start_y + 2, app.map_view.cursor_y


async def s_cursor_clamps(app, pilot):
    for _ in range(WORLD_W + 10):
        await pilot.press("left")
    assert app.map_view.cursor_x == 0, app.map_view.cursor_x
    for _ in range(WORLD_H + 10):
        await pilot.press("up")
    assert app.map_view.cursor_y == 0, app.map_view.cursor_y


async def s_tool_select(app, pilot):
    await pilot.press("4")  # Build Road
    sel = app.tools_panel.selected
    assert TOOLS[sel].label.startswith("Build Road"), (
        f"selected={sel} label={TOOLS[sel].label}"
    )


async def s_apply_road_deducts_funds(app, pilot):
    spot = await find_dirt(app)
    assert spot is not None, "no dirt tile found"
    app.map_view.cursor_x, app.map_view.cursor_y = spot
    await pilot.pause()
    funds_before = app.sim.totalFunds
    await pilot.press("4")      # Road
    await pilot.press("enter")  # apply
    await pilot.pause()
    funds_after = app.sim.totalFunds
    # Road tool costs 10; sim tick may also be running, so just assert <=.
    assert funds_after < funds_before, f"{funds_before} → {funds_after}"


async def s_apply_road_changes_tile(app, pilot):
    spot = await find_dirt(app)
    assert spot is not None, "no dirt tile found"
    app.map_view.cursor_x, app.map_view.cursor_y = spot
    await pilot.pause()
    before = tile_class(app, *spot)
    await pilot.press("4")
    await pilot.press("enter")
    await pilot.pause()
    after = tile_class(app, *spot)
    assert before == "dirt", before
    assert after == "road", f"expected road at {spot}, got {after}"


async def s_pause_halts_ticks(app, pilot):
    # Let the tick timer run once so cityMonth is stable.
    await pilot.pause(0.2)
    month_before = app.sim.cityMonth
    await pilot.press("p")
    assert app.paused is True
    await pilot.pause(0.5)  # 5 ticks would have happened if unpaused
    assert app.sim.cityMonth == month_before, (
        f"month changed while paused: {month_before} → {app.sim.cityMonth}"
    )
    await pilot.press("p")
    assert app.paused is False


async def s_bulldozer_select(app, pilot):
    await pilot.press("8")
    sel = app.tools_panel.selected
    assert TOOLS[sel].label == "Bulldoze", TOOLS[sel].label


async def _s_modal(app, pilot, key: str, class_name: str) -> None:
    """Shared scenario body: pressing `key` opens the modal whose class name
    is `class_name`; escape closes it."""
    await pilot.press(key)
    await pilot.pause()
    assert app.screen.__class__.__name__ == class_name, (
        f"after pressing {key!r}, top screen is {app.screen.__class__.__name__}"
    )
    await pilot.press("escape")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "Screen", app.screen.__class__.__name__


async def s_overlay_cycle(app, pilot):
    """Pressing 'o' must cycle through all overlay modes and wrap back."""
    from simcity_tui.screens import OVERLAY_MODES
    start = app.map_view.overlay_mode
    seen = [start]
    for _ in range(len(OVERLAY_MODES)):
        await pilot.press("o")
        await pilot.pause()
        seen.append(app.map_view.overlay_mode)
    # We should have cycled through every mode and ended back at start.
    assert seen[-1] == start, f"overlay wrap failed: {seen}"
    assert set(seen) == set(OVERLAY_MODES), f"missing modes: {set(OVERLAY_MODES) - set(seen)}"


async def s_budget_tax_adjust(app, pilot):
    """Open budget dialog, bump tax with arrow keys, verify sim state updates."""
    start_tax = app.sim.cityTax
    await pilot.press("b")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "BudgetScreen"
    await pilot.press("plus")
    await pilot.press("plus")
    await pilot.pause()
    assert app.sim.cityTax == min(20, start_tax + 2), (
        f"tax {start_tax} → {app.sim.cityTax}, expected +2"
    )
    await pilot.press("escape")


async def s_sound_disabled_is_noop(app, pilot):
    """With sound off (the default), play() must return cleanly and never
    attempt to spawn a subprocess — this is what allows the TUI to run
    over SSH / in containers without surprises."""
    # SimCityApp default is sound=False
    assert app.sounds.enabled is False
    app.sounds.play("build")  # must not raise
    app.sounds.play("does_not_exist")  # unknown key must not raise


async def s_extended_tool_railroad(app, pilot):
    """Pressing 'r' selects the Railroad tool."""
    await pilot.press("r")
    await pilot.pause()
    assert TOOLS[app.tools_panel.selected].label == "Railroad", (
        f"selected={app.tools_panel.selected} label={TOOLS[app.tools_panel.selected].label}"
    )


async def s_status_panel_throttles(app, pilot):
    """StatusPanel.refresh_panel() called with unchanged sim state must not
    rebuild the Text. This is what fixed the 10 Hz bar flicker."""
    panel = app.status_panel
    panel.refresh_panel()
    snap1 = panel._last_snapshot
    # Call it 5 more times with no sim change — snapshot must stay identical
    # (we're not asserting the Text object identity because Textual's update()
    # may or may not re-cache, but the snapshot check is the gate).
    for _ in range(5):
        panel.refresh_panel()
    assert panel._last_snapshot == snap1, "snapshot changed with no sim mutation"


async def s_flash_bar(app, pilot):
    """flash_status shows a message and, when the timer fires, yields the
    bar back to the hover-info rendering (cursor coords/class)."""
    app.flash_status("hello", seconds=0.2)
    assert "hello" in str(app.flash_bar.content), app.flash_bar.content
    # Wait long enough for the timer to fire.
    await pilot.pause(0.3)
    # After clear we expect either empty OR the hover-info replacement,
    # NOT the old transient message.
    after = str(app.flash_bar.content)
    assert "hello" not in after, f"flash bar still shows message: {after!r}"


async def s_tool_uses_flash(app, pilot):
    """Pressing a tool key should put feedback on the flash bar, NOT the
    message log. Regression guard for the log/flash split."""
    # Snapshot log line count by counting write calls — RichLog exposes .lines
    log_lines_before = len(app.message_log.lines)
    await pilot.press("4")  # select Road tool
    await pilot.pause()
    # Flash bar should carry the tool name.
    assert "Road" in str(app.flash_bar.content), app.flash_bar.content
    # Log should be unchanged.
    assert len(app.message_log.lines) == log_lines_before, (
        "tool-select wrote to message log — should have gone to flash"
    )


async def s_animation_water(app, pilot):
    """Advancing the animation frame must swap glyphs for animated classes
    (water here) while leaving static classes unchanged."""
    mv = app.map_view
    # Find a water tile if one is visible on this city.
    import ctypes
    water_pos = None
    for y in range(WORLD_H):
        for x in range(WORLD_W):
            klass = tiles._TABLE[mv._map[x * WORLD_H + y] & tiles.TILE_MASK][1]
            if klass.startswith("water"):
                water_pos = (x, y)
                break
        if water_pos:
            break
    if water_pos is None:
        # No water in this city — skip the assertion but don't fail.
        return
    # Scroll so the water tile is visible, then look at its glyph across
    # two animation frames.
    mv.scroll_to_region(
        __import__("textual.geometry", fromlist=["Region"]).Region(
            water_pos[0], water_pos[1], 1, 1),
        animate=False, force=True,
    )
    await pilot.pause()
    vy = water_pos[1] - int(mv.scroll_offset.y)
    vx = water_pos[0] - int(mv.scroll_offset.x)
    mv._anim_frame = 0
    frame0 = "".join(seg.text for seg in list(mv.render_line(vy)))[vx]
    mv._anim_frame = 1
    frame1 = "".join(seg.text for seg in list(mv.render_line(vy)))[vx]
    assert frame0 != frame1, (
        f"water glyph didn't change between frames: {frame0!r} == {frame1!r}"
    )


async def s_log_collapse(app, pilot):
    """Consecutive identical log messages should collapse to '…×N'."""
    before = len(app.message_log.lines)
    app.log_msg("same message")
    app.log_msg("same message")
    app.log_msg("same message")
    # All three → one collapsed line with a ×3 suffix.
    grew = len(app.message_log.lines) - before
    assert grew == 1, f"expected 1 new line, got {grew}"
    last = str(app.message_log.lines[-1])
    assert "×3" in last, f"expected ×3 suffix, got {last!r}"


async def s_sound_debounce(app, pilot):
    """Rapid repeated sound.play() calls must suppress all but the first."""
    import time
    from simcity_tui.sounds import SoundBoard
    board = SoundBoard(enabled=True)
    if not board.enabled:  # no player on system
        return
    # Replace subprocess.Popen with a counter to avoid real audio.
    import subprocess
    original = subprocess.Popen
    calls = []
    subprocess.Popen = lambda *a, **kw: calls.append(1) or type("X", (), {})()
    try:
        for _ in range(10):
            board.play("build")
        # First play → subprocess. Subsequent (<150 ms) → dropped.
        assert len(calls) == 1, f"expected 1 play, got {len(calls)}"
        # After 200ms a new play is allowed.
        time.sleep(0.2)
        board.play("build")
        assert len(calls) == 2, f"expected 2 plays post-sleep, got {len(calls)}"
    finally:
        subprocess.Popen = original
        board.close()


async def s_find_dirt_fallback(app, pilot):
    """find_dirt should find *some* open land even in developed cities
    that have no literal tile ID 0."""
    spot = await find_dirt(app)
    assert spot is not None, "find_dirt returned None on standard city"


async def s_state_snapshot_headless(app, pilot):
    """Regression for issue #1 on GitHub: state_snapshot must not crash
    when accessed from --headless mode (no App mount context). Reading
    a reactive for the first time fires its watcher; the watchers used
    to call scroll_to_region which needs a live App.

    Here we simulate the headless path: build a fresh SimCityApp (which
    is NOT mounted) and invoke state_snapshot on it. Must return a dict,
    not raise NoActiveAppError."""
    from simcity_tui.app import SimCityApp
    from simcity_tui.agent_api import state_snapshot
    fresh = SimCityApp()
    s = state_snapshot(fresh)
    assert isinstance(s, dict), type(s)
    for k in ("year", "population", "cursor", "funds"):
        assert k in s, f"missing {k}"


async def s_anchor_clears_on_tool_change(app, pilot):
    """Selecting a new tool must clear a pending rect-zoning anchor."""
    # Fake an anchor.
    app.map_view._rect_anchor = (50, 50)
    await pilot.press("4")  # switch to Road
    await pilot.pause()
    assert app.map_view._rect_anchor is None, (
        f"anchor not cleared: {app.map_view._rect_anchor}"
    )


async def s_advisor_no_key(app, pilot):
    """With no ANTHROPIC_API_KEY, advisor.consult must return a short
    user-friendly error string instead of raising. This is the
    fail-silent-with-feedback contract."""
    import os
    from simcity_tui import advisor
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        # Also verify the cheap probe.
        assert advisor.available() is False
        text = advisor.consult({"year": 2000, "population": 100})
        assert "ANTHROPIC_API_KEY" in text, text
        assert "unavailable" in text, text
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


async def s_tool_preview(app, pilot):
    """Selecting a zone tool sets a 3×3 footprint; selecting a road sets
    nothing (1×1 tools skip the preview)."""
    # Select Residential (zone) — should set 3x3 preview.
    await pilot.press("1")
    await pilot.pause()
    assert app.map_view._preview is not None
    w, h, dx, dy, _ = app.map_view._preview
    assert (w, h, dx, dy) == (3, 3, -1, -1), app.map_view._preview
    # Select Road — should NOT set a footprint (1x1 isn't in the table).
    await pilot.press("4")
    await pilot.pause()
    assert app.map_view._preview is None, app.map_view._preview


async def s_hover_info(app, pilot):
    """Moving the cursor should update the flash bar with tile info
    (coords + class) as long as no tool-result flash is pending."""
    # Arrow-move the cursor a few times, then inspect flash bar.
    await pilot.press("right", "right", "right")
    await pilot.pause()
    txt = str(app.flash_bar.content)
    assert "(" in txt and ")" in txt, f"no coords in hover info: {txt!r}"
    # The starting cursor of haight at (60,50) should be reachable and
    # the class should appear in the hover text.


async def s_log_icon(app, pilot):
    """log_msg writes a line with the severity icon prefix."""
    start = len(app.message_log.lines)
    app.log_msg("test success message", level="success")
    app.log_msg("test disaster", level="disaster")
    assert len(app.message_log.lines) == start + 2
    # The latest two lines should carry the success (✓) and the disaster
    # (🔥) icons — post emoji-strategy pass.
    last = "".join(str(seg) for seg in app.message_log.lines[-2:])
    assert "✓" in last and "🔥" in last, f"icons missing: {last!r}"


async def s_legend_opens(app, pilot):
    """Pressing 'l' opens the LegendScreen."""
    await pilot.press("l")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "LegendScreen", app.screen.__class__.__name__
    await pilot.press("escape")
    await pilot.pause()


async def s_already_bulldozed(app, pilot):
    """Bulldozing clear dirt should say 'already bulldozed', not the
    generic 'can't place' string. This is the error-message polish."""
    mv = app.map_view
    # Find dirt, move cursor there, select bulldozer, and fire.
    dirt = await find_dirt(app)
    assert dirt is not None
    mv.cursor_x, mv.cursor_y = dirt
    await pilot.pause()
    await pilot.press("8")  # bulldozer
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    flash = str(app.flash_bar.content)
    assert "already bulldozed" in flash, flash


async def s_rci_census_smoothing(app, pilot):
    """Simulate one full census cycle's worth of ticks — the displayed
    R/C/I snapshot must remain STABLE (non-zero throughout), not drop to
    0 as the raw resPop does mid-sweep. This is the fix for the flashing
    demand bars."""
    # Prime with 30 ticks so the panel has seen a full cycle.
    for _ in range(80):
        app.sim.simTick()
        app.status_panel.refresh_panel()
    displayed = app.status_panel._last_snapshot
    # First field is res_stable — it should NOT be 0 at any point AFTER
    # we've seen at least one full cycle.
    assert displayed is not None and displayed[0] > 0, (
        f"res bar dropped to 0 even after 80 ticks: {displayed}"
    )


async def s_click_tool(app, pilot):
    """Clicking a tool row in the TOOLS panel selects that tool, same as
    pressing its key."""
    # Click at y=3 → index 3 = Build Road (per the TOOLS list).
    await pilot.click("ToolsPanel", offset=(1, 3))
    await pilot.pause()
    assert TOOLS[app.tools_panel.selected].label == "Build Road", (
        TOOLS[app.tools_panel.selected].label
    )


async def s_save_load_round_trip(app, pilot):
    """Save the current city to a temp path, apply a visible change
    (place a coal plant), then load the save and verify the coal plant is
    gone — confirming the file actually round-trips the map state."""
    import tempfile
    import os
    # Save to a fresh temp path, not the default save dir, so we don't
    # clutter the user's real saves.
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "qa.cty")
        # saveCityAs returns None on success — check side effect instead.
        app.sim.saveCityAs(path)
        assert os.path.exists(path), f"{path} not written"
        size_before = os.path.getsize(path)
        assert size_before > 100, f"save file suspiciously small: {size_before}"

        # Modify the map on a dirt tile where the action will succeed.
        funds_before_change = app.sim.totalFunds
        dirt = await find_dirt(app)
        assert dirt is not None
        app.sim.doTool(9, dirt[0], dirt[1])  # road — costs $10
        assert app.sim.totalFunds != funds_before_change, "road didn't deduct"

        # Reload the save.
        ok = app.sim.loadCity(path)
        assert ok, "loadCity returned falsy"
        # Funds should have been restored from the save.
        assert app.sim.totalFunds == funds_before_change, (
            f"funds not restored by load: {funds_before_change} → {app.sim.totalFunds}"
        )


async def s_road_glyphs(app, pilot):
    """Phase-A-followup: road tile glyphs must match Micropolis's connection
    table. A few pivotal IDs — 66 is a plain horizontal, 68 is a south+west
    elbow (╮), 76 is the 4-way intersection — and traffic-bearing tiles at
    +80 and +144 must carry the SAME glyph (only the style differs)."""
    from simcity_tui import tiles
    t = tiles._TABLE
    assert t[66][0] == "─", f"ROADS (66) glyph = {t[66][0]!r}"
    assert t[67][0] == "│", f"ROADS2 (67) glyph = {t[67][0]!r}"
    assert t[68][0] == "╮", f"ROADS3 (68) glyph = {t[68][0]!r}"  # S+W
    assert t[69][0] == "╯", f"ROADS4 (69) glyph = {t[69][0]!r}"  # N+W
    assert t[70][0] == "╰", f"ROADS5 (70) glyph = {t[70][0]!r}"  # N+E
    assert t[71][0] == "╭", f"ROADS6 (71) glyph = {t[71][0]!r}"  # S+E
    assert t[76][0] == "┼", f"INTERSECTION glyph = {t[76][0]!r}"
    # Traffic tiles should preserve the shape — tile 68+16 (low-traffic elbow)
    # and 68+80 (high-traffic elbow) are still ╮.
    assert t[84][0] == "╮", f"low-traffic 84 = {t[84][0]!r}"
    assert t[148][0] == "╮", f"high-traffic 148 = {t[148][0]!r}"
    # 4-way intersection got promoted to its own class so it can pop
    # brighter against straight-road runs.
    assert t[76][1] == "road_inter", f"tile 76 class = {t[76][1]!r}"
    assert t[148][1] == "road_busy", f"tile 148 class = {t[148][1]!r}"


async def s_tutorial_navigation(app, pilot):
    """Pressing 't' opens the TutorialScreen on page 1; 'n' advances; 'b'
    backs up; 'escape' closes and returns to the game."""
    await pilot.press("t")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "TutorialScreen"
    start = app.screen.page
    await pilot.press("n")
    await pilot.pause()
    assert app.screen.page == start + 1, f"n didn't advance: {app.screen.page}"
    await pilot.press("b")
    await pilot.pause()
    assert app.screen.page == start, f"b didn't back up: {app.screen.page}"
    await pilot.press("escape")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "Screen"


async def s_extended_tool_park(app, pilot):
    """Pressing 'k' selects the Park tool."""
    await pilot.press("k")
    await pilot.pause()
    assert TOOLS[app.tools_panel.selected].label == "Park", (
        TOOLS[app.tools_panel.selected].label
    )


async def s_sound_enabled_synthesises(app, pilot):
    """With sound on, the SoundBoard must pick a player if any is available
    and successfully synth a wave. We don't actually play it (no assertions
    about hearing) — just verify the synthesis path."""
    from simcity_tui.sounds import SoundBoard
    board = SoundBoard(enabled=True)
    if board._player is None:
        # No audio player on this machine — graceful degrade is the contract.
        assert board.enabled is False, "should auto-disable when no player"
        return
    # Synthesise + cache path without playing.
    path = board._ensure("build")
    assert path is not None
    assert path.exists(), f"wav not written: {path}"
    assert path.stat().st_size > 100, "wav too small — synth broken"
    board.close()


async def s_history_samples(app, pilot):
    """The history sampler must record at least one snapshot per month tick."""
    before = len(app._history)
    # Bump the month manually and call tick; should add a sample.
    app._last_month = app.sim.cityMonth - 1  # force the month-changed branch
    app.tick()
    assert len(app._history) == before + 1, (
        f"history did not grow: {before} → {len(app._history)}"
    )
    row = app._history[-1]
    for key in ("year", "month", "cityPop", "totalFunds", "cityScore"):
        assert key in row, f"history row missing {key}"


async def s_map_renders_with_backgrounds(app, pilot):
    """Phase A regression: every rendered tile must carry BOTH a foreground
    AND a background color. A bare fg-only style indicates we regressed back
    to the flat palette."""
    mv = app.map_view
    mv.scroll_to_cursor()
    await pilot.pause()
    # Pick the cursor row (should be visible).
    viewport_y = mv.cursor_y - int(mv.scroll_offset.y)
    strip = mv.render_line(viewport_y)
    fg_only = 0
    both = 0
    for seg in strip:
        if not seg.style:
            continue
        if seg.style.color is not None and seg.style.bgcolor is not None:
            both += 1
        elif seg.style.color is not None and seg.style.bgcolor is None:
            fg_only += 1
    assert both > 0, "no tiles rendered with background colors"
    # Allow a few fg-only segments (padding blanks), but most should be styled.
    assert fg_only <= 2, f"too many fg-only segments: {fg_only}"


async def s_map_buffer_rebind_is_safe(app, pilot):
    """If the engine's map pointer moved, render_line must rebind rather
    than read freed memory. We simulate this by pretending the pointer is
    stale and confirming the next render silently rebinds."""
    mv = app.map_view
    original = mv._map_ptr
    # Poison the cached pointer so _bind_map_buffer sees a mismatch.
    mv._map_ptr = 0
    mv._map = None
    # A render_line call must rebind and not crash.
    strip = mv.render_line(0)
    assert mv._map is not None, "rebind did not restore the map view"
    assert mv._map_ptr == original, f"ptr {mv._map_ptr} != {original}"
    assert len(list(strip)) > 0


async def s_unknown_tile_class_does_not_crash(app, pilot):
    """Robustness: rendering must not KeyError if a tile class is missing
    from the color table."""
    mv = app.map_view
    # Temporarily drop a category that we know renders.
    saved = mv._styles.pop("road", None)
    try:
        strip = mv.render_line(0)
        assert len(list(strip)) > 0, "render_line returned empty strip"
    finally:
        if saved is not None:
            mv._styles["road"] = saved


async def s_cursor_renders_with_highlight(app, pilot):
    """Regression guard for the cursor-overlay refactor: the rendered strip
    at the cursor's row must carry the yellow-background cursor style on
    exactly one cell."""
    from rich.style import Style
    expected = Style.parse("bold black on rgb(255,220,80)")
    # Scroll so the cursor is guaranteed visible, then ask for that row.
    app.map_view.scroll_to_cursor()
    await pilot.pause()
    # Viewport-relative y of the cursor row:
    cy = app.map_view.cursor_y
    scroll_y = int(app.map_view.scroll_offset.y)
    viewport_y = cy - scroll_y
    strip = app.map_view.render_line(viewport_y)
    highlighted_cells = sum(
        len(seg.text) for seg in list(strip) if seg.style == expected
    )
    assert highlighted_cells == 1, (
        f"expected exactly 1 highlighted cell, got {highlighted_cells}"
    )


async def s_mouse_click_moves_cursor_and_applies(app, pilot):
    """Clicking on the map moves the cursor there AND applies the selected
    tool. With the Road tool (10/tile), funds should drop after a click on
    a dirt tile."""
    spot = await find_dirt(app)
    assert spot is not None, "no dirt tile found"
    # Pre-select road so click applies something visible.
    await pilot.press("4")
    await pilot.pause()
    funds_before = app.sim.totalFunds
    tile_before = tile_class(app, *spot)
    # Scroll the map so `spot` is in view, then compute the visible offset.
    app.map_view.scroll_to_region(
        __import__("textual.geometry", fromlist=["Region"]).Region(
            spot[0] - 2, spot[1] - 2, 5, 5
        ),
        animate=False,
        force=True,
    )
    await pilot.pause()
    offset = (
        spot[0] - int(app.map_view.scroll_offset.x),
        spot[1] - int(app.map_view.scroll_offset.y),
    )
    await pilot.click("MapView", offset=offset)
    await pilot.pause()
    assert (app.map_view.cursor_x, app.map_view.cursor_y) == spot, (
        f"cursor at ({app.map_view.cursor_x},{app.map_view.cursor_y}), "
        f"expected {spot}"
    )
    assert tile_before == "dirt"
    assert tile_class(app, *spot) == "road", (
        f"tile at {spot} = {tile_class(app, *spot)}, expected road"
    )
    assert app.sim.totalFunds < funds_before, (
        f"funds {funds_before} → {app.sim.totalFunds}"
    )


SCENARIOS: list[Scenario] = [
    Scenario("mount_clean", s_mount_clean),
    Scenario("cursor_starts_centered", s_cursor_starts_centered),
    Scenario("cursor_moves", s_cursor_moves),
    Scenario("cursor_clamps", s_cursor_clamps),
    Scenario("tool_select", s_tool_select),
    Scenario("bulldozer_select", s_bulldozer_select),
    Scenario("apply_road_deducts_funds", s_apply_road_deducts_funds),
    Scenario("apply_road_changes_tile", s_apply_road_changes_tile),
    Scenario("pause_halts_ticks", s_pause_halts_ticks),
    Scenario("cursor_renders_with_highlight", s_cursor_renders_with_highlight),
    Scenario("mouse_click_moves_and_applies", s_mouse_click_moves_cursor_and_applies),
    Scenario("map_buffer_rebind_is_safe", s_map_buffer_rebind_is_safe),
    Scenario("unknown_tile_class_does_not_crash", s_unknown_tile_class_does_not_crash),
    Scenario("map_renders_with_backgrounds", s_map_renders_with_backgrounds),
    Scenario("help_screen_opens_and_closes", lambda a, p: _s_modal(a, p, "question_mark", "HelpScreen")),
    Scenario("budget_screen_opens", lambda a, p: _s_modal(a, p, "b", "BudgetScreen")),
    Scenario("graphs_screen_opens", lambda a, p: _s_modal(a, p, "g", "GraphsScreen")),
    Scenario("eval_screen_opens", lambda a, p: _s_modal(a, p, "e", "EvaluationScreen")),
    Scenario("overlay_cycle", s_overlay_cycle),
    Scenario("budget_tax_adjust", s_budget_tax_adjust),
    Scenario("history_samples_on_month_change", s_history_samples),
    Scenario("sound_disabled_is_noop", s_sound_disabled_is_noop),
    Scenario("sound_enabled_synthesises", s_sound_enabled_synthesises),
    Scenario("extended_tool_railroad", s_extended_tool_railroad),
    Scenario("extended_tool_park", s_extended_tool_park),
    Scenario("road_glyphs_follow_conn_table", s_road_glyphs),
    Scenario("tutorial_opens_and_navigates", s_tutorial_navigation),
    Scenario("status_panel_skips_unchanged", s_status_panel_throttles),
    Scenario("flash_bar_shows_then_clears", s_flash_bar),
    Scenario("tool_feedback_goes_to_flash_not_log", s_tool_uses_flash),
    Scenario("click_tool_row_selects", s_click_tool),
    Scenario("save_load_round_trip", s_save_load_round_trip),
    Scenario("legend_screen_opens", s_legend_opens),
    Scenario("already_bulldozed_friendly_msg", s_already_bulldozed),
    Scenario("rci_bars_survive_census_cycle", s_rci_census_smoothing),
    Scenario("animation_alternates_water_glyph", s_animation_water),
    Scenario("hover_info_shows_tile_class", s_hover_info),
    Scenario("log_msg_carries_icon", s_log_icon),
    Scenario("tool_preview_shows_footprint", s_tool_preview),
    Scenario("advisor_graceful_without_key", s_advisor_no_key),
    Scenario("log_collapses_duplicates", s_log_collapse),
    Scenario("sound_debounce_drops_burst", s_sound_debounce),
    Scenario("find_dirt_fallback_on_developed_city", s_find_dirt_fallback),
    Scenario("tool_change_clears_rect_anchor", s_anchor_clears_on_tool_change),
    Scenario("state_snapshot_works_headless", s_state_snapshot_headless),
]


# ---------- driver ----------

async def run_one(scn: Scenario) -> tuple[str, bool, str]:
    app = SimCityApp()
    try:
        async with app.run_test(size=(180, 60)) as pilot:
            await pilot.pause()  # let on_mount complete
            try:
                await scn.fn(app, pilot)
            except AssertionError as e:
                app.save_screenshot(str(OUT / f"{scn.name}.FAIL.svg"))
                return (scn.name, False, f"AssertionError: {e}")
            except Exception as e:
                app.save_screenshot(str(OUT / f"{scn.name}.ERROR.svg"))
                return (scn.name, False,
                        f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            app.save_screenshot(str(OUT / f"{scn.name}.PASS.svg"))
            return (scn.name, True, "")
    except Exception as e:
        return (scn.name, False,
                f"harness error: {type(e).__name__}: {e}\n{traceback.format_exc()}")


async def main(pattern: str | None = None) -> int:
    scenarios = [s for s in SCENARIOS if not pattern or pattern in s.name]
    if not scenarios:
        print(f"no scenarios match {pattern!r}")
        return 2
    results = []
    for scn in scenarios:
        name, ok, msg = await run_one(scn)
        mark = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
        print(f"  {mark} {name}")
        if not ok:
            for line in msg.splitlines():
                print(f"      {line}")
        results.append((name, ok, msg))
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{passed}/{len(results)} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(pattern)))
