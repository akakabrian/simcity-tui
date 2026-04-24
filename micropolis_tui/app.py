"""Textual app — 4-panel SimCity TUI around the Micropolis engine."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Region, Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Footer, Header, RichLog, Static

from . import tiles
from .engine import WORLD_H, WORLD_W, new_sim
from .screens import (
    OVERLAY_MODES,
    AdvisorScreen,
    BudgetScreen,
    EvaluationScreen,
    GraphsScreen,
    HelpScreen,
    LegendScreen,
    LoadScreen,
    SaveScreen,
    TutorialScreen,
    overlay_buffer,
    overlay_glyph_and_color,
)
from .sounds import SoundBoard
from .music import MusicPlayer
# micropolisengine is loaded by engine.py; re-import for constants.
import micropolisengine as me  # pyright: ignore[reportMissingImports]


@dataclass(frozen=True)
class ToolDef:
    key: str
    label: str
    code: int
    cost: int = 0
    # Sample glyph + rich style, rendered alongside the tool name so the
    # player gets a visual preview of what will be placed.
    glyph: str = " "
    style: str = ""


# Placement footprints per tool, as (width, height, dx_from_cursor,
# dy_from_cursor). Missing tools default to 1×1 at the cursor.
# Zones are centered on the cursor (top-left offset -1,-1). Big civic
# buildings put cursor at the top-left in Micropolis.
_TOOL_FOOTPRINT: dict[int, tuple[int, int, int, int]] = {
    me.TOOL_RESIDENTIAL:    (3, 3, -1, -1),
    me.TOOL_COMMERCIAL:     (3, 3, -1, -1),
    me.TOOL_INDUSTRIAL:     (3, 3, -1, -1),
    me.TOOL_POLICESTATION:  (3, 3, -1, -1),
    me.TOOL_FIRESTATION:    (3, 3, -1, -1),
    me.TOOL_PARK:           (1, 1,  0,  0),
    me.TOOL_COALPOWER:      (4, 4, -1, -1),
    me.TOOL_NUCLEARPOWER:   (4, 4, -1, -1),
    me.TOOL_STADIUM:        (4, 4, -1, -1),
    me.TOOL_SEAPORT:        (4, 4, -1, -1),
    me.TOOL_AIRPORT:        (6, 6, -1, -1),
}


TOOLS: list[ToolDef] = [
    # Numeric hotkeys 1–9 for the most common tools. Glyphs match what
    # tiles.py renders for a fresh zone of each type.
    ToolDef("1", "Zone Residential (R)",       me.TOOL_RESIDENTIAL,   100,
            "R", "bold rgb(80,200,120) on rgb(10,28,15)"),
    ToolDef("2", "Zone Commercial (C)",        me.TOOL_COMMERCIAL,    100,
            "C", "bold rgb(80,140,220) on rgb(10,20,38)"),
    ToolDef("3", "Zone Industrial (I)",        me.TOOL_INDUSTRIAL,    100,
            "I", "bold rgb(220,90,90) on rgb(40,15,15)"),
    ToolDef("4", "Build Road",                 me.TOOL_ROAD,           10,
            "─", "bold rgb(220,220,220) on rgb(28,28,30)"),
    ToolDef("5", "Coal Power Plant ($3000)",   me.TOOL_COALPOWER,    3000,
            "▣", "bold rgb(230,90,90) on rgb(55,20,20)"),
    ToolDef("6", "Police Station ($500)",      me.TOOL_POLICESTATION, 500,
            "◉", "bold rgb(120,160,240) on rgb(20,30,55)"),
    ToolDef("7", "Fire Station ($500)",        me.TOOL_FIRESTATION,   500,
            "♨", "bold rgb(255,120,70) on rgb(55,25,15)"),
    ToolDef("8", "Bulldoze",                   me.TOOL_BULLDOZER,       1,
            " ", "on rgb(120,96,64)"),
    ToolDef("9", "Power Line",                 me.TOOL_WIRE,            5,
            "═", "bold rgb(255,220,80) on rgb(40,32,12)"),
    # Letter hotkeys for the advanced / heavy-infrastructure tools.
    ToolDef("r", "Railroad",                   me.TOOL_RAILROAD,       20,
            "═", "bold rgb(170,140,110) on rgb(35,28,20)"),
    ToolDef("k", "Park",                       me.TOOL_PARK,           10,
            "♣", "bold rgb(70,150,60) on rgb(18,40,20)"),
    ToolDef("z", "Stadium ($5000)",            me.TOOL_STADIUM,      5000,
            "◎", "bold rgb(240,200,120) on rgb(50,35,15)"),
    ToolDef("w", "Seaport ($3000)",            me.TOOL_SEAPORT,      3000,
            "⚓", "bold rgb(180,200,240) on rgb(20,30,50)"),
    ToolDef("a", "Airport ($10000)",           me.TOOL_AIRPORT,     10000,
            "✈", "bold rgb(230,230,230) on rgb(40,40,45)"),
    ToolDef("n", "Nuclear Plant ($5000)",      me.TOOL_NUCLEARPOWER, 5000,
            "☢", "bold rgb(250,250,120) on rgb(55,50,15)"),
]

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _why_failed(tool: "ToolDef", mv: "MapView", x: int, y: int) -> str:
    """Diagnose why TOOLRESULT_FAILED happened by inspecting the tile.
    Returns a short human-readable reason string (with rich markup)."""
    mv._bind_map_buffer()
    tile_id = mv._map[x * WORLD_H + y] & tiles.TILE_MASK
    klass = tiles._TABLE[tile_id][1]

    # Bulldozer on an already-clear tile.
    if tool.code == me.TOOL_BULLDOZER:
        if klass == "dirt":
            return "[yellow]already bulldozed (clear dirt)[/]"
        if klass in ("water_shallow", "water_deep"):
            return "[red]✗ can't bulldoze water directly[/]"
        return f"[red]✗ nothing here to bulldoze ({klass})[/]"

    # Water is a universal blocker for everything except bulldoze.
    if klass in ("water_shallow", "water_deep"):
        return "[red]✗ can't build on water[/] — bulldoze coastline first"

    # Zoning on trees.
    if klass in ("tree", "forest"):
        return "[red]✗ can't zone on trees[/] — bulldoze first"

    # Building on existing road / power / rail.
    if klass in ("road", "road_busy", "road_pwr", "road_rail", "bridge"):
        return "[red]✗ there's already a road here[/]"
    if klass == "power":
        return "[red]✗ there's already a power line here[/]"
    if klass == "rail":
        return "[red]✗ there's already a railroad here[/]"

    # Trying to zone on top of an existing zone / civic building.
    if klass.startswith(("resid", "comm", "indus")):
        return "[red]✗ this tile is already zoned[/]"
    if klass in ("plant", "nuclear", "police", "fire_st", "stadium",
                 "harbor", "airport"):
        return f"[red]✗ a {klass.replace('_', ' ')} is already here[/]"

    # Zone tools need a 3×3 clearing.
    if tool.code in (me.TOOL_RESIDENTIAL, me.TOOL_COMMERCIAL, me.TOOL_INDUSTRIAL):
        return "[red]✗ need a 3×3 clear patch of dirt for a zone[/]"
    # Large civic buildings need bigger footprints.
    if tool.code in (me.TOOL_COALPOWER, me.TOOL_NUCLEARPOWER, me.TOOL_STADIUM,
                     me.TOOL_SEAPORT, me.TOOL_AIRPORT):
        return "[red]✗ need a larger clear patch (check footprint)[/]"
    # Fallback.
    return f"[red]✗ can't place {tool.label} here ({klass})[/]"


class MapView(ScrollView):
    """Renders the 120×100 tile grid with a highlighted cursor.

    Uses Textual's line-rendering API so (a) only visible rows are
    rendered and (b) cursor moves only repaint the two affected rows
    instead of the whole map."""

    DEFAULT_CSS = """
    MapView { padding: 0; }
    """

    cursor_x: reactive[int] = reactive(WORLD_W // 2)
    cursor_y: reactive[int] = reactive(WORLD_H // 2)

    class ToolApply(Message):
        """Posted when the user clicks/drags on the map. If (x1,y1)==(x2,y2)
        it's a single click; otherwise it's a drag segment."""
        def __init__(self, x1: int, y1: int, x2: int, y2: int) -> None:
            self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
            super().__init__()

    class RectApply(Message):
        """Posted on right-click when a prior left-click set an anchor.
        The app fills the rectangle from (anchor_x,anchor_y) to (x,y) with
        the selected tool, stepping by the tool's footprint for zones so
        we don't generate overlapping 3×3 placements."""
        def __init__(self, x1: int, y1: int, x2: int, y2: int) -> None:
            # Normalise so (x1,y1) is top-left.
            self.x1 = min(x1, x2)
            self.y1 = min(y1, y2)
            self.x2 = max(x1, x2)
            self.y2 = max(y1, y2)
            super().__init__()

    def __init__(self, sim) -> None:
        super().__init__()
        self.sim = sim
        self._drag_last: tuple[int, int] | None = None
        self.virtual_size = Size(WORLD_W, WORLD_H)
        # Pre-parse fg+bg styles once. Per-cell Style.parse() would dominate.
        self._styles: dict[str, Style] = {
            klass: Style.parse(tiles.style_for(klass)) for klass in tiles.COLOR
        }
        self._cursor_style: Style = Style.parse(
            "bold black on rgb(255,220,80)"
        )
        # Fallback so an unknown tile class renders visibly instead of raising.
        self._unknown_style: Style = Style.parse("bold rgb(255,0,255) on black")
        # Overlay mode ("off" → normal tile render; see screens.OVERLAY_MODES).
        self.overlay_mode: str = "off"
        self._overlay_style_cache: Style | None = None
        # Animation frame counter — drives water ripples, cursor blink,
        # industrial heat pulse, power glow, and traffic flow on busy roads.
        # Tick rate set by SimCityApp's set_interval (see on_mount).
        self._anim_frame: int = 0
        # Pre-parsed "pulse" variants of the zone/infra styles the animation
        # cycles between. We build these lazily; first render populates them.
        self._pulse_styles: dict[str, tuple[Style, Style]] = {}
        # Placement preview — set by SimCityApp whenever the current tool
        # changes. Tuple of (w, h, dx, dy, can_place_bool). None = no preview.
        self._preview: tuple[int, int, int, int, bool] | None = None
        # Pre-parsed tint styles for the preview overlay.
        self._preview_ok = Style.parse("on rgb(40,90,40)")
        self._preview_bad = Style.parse("on rgb(90,30,30)")
        # Per-refresh-pass caches: the first render_line() call in a pass
        # probes the engine (ptr rebind, overlay fetch); subsequent rows
        # in the same pass reuse those values. Saves 99× FFI calls per
        # frame for both probes.
        self._last_pass_row: int = -1
        self._pass_overlay_cache: bytes | None = None
        # Rectangle-zoning anchor — set on left-click, consumed on
        # right-click, cleared on tool change or escape.
        self._rect_anchor: tuple[int, int] | None = None
        self._anchor_style = Style.parse("bold black on rgb(180,120,60)")
        # Zero-copy live view of Micropolis's internal map as a uint16 array.
        # Layout is column-major: index = x * WORLD_H + y. We re-bind if the
        # engine ever reallocates the buffer (see _bind_map_buffer).
        self._map_ptr: int = 0
        self._map: ctypes.Array | None = None
        self._bind_map_buffer()
        # Serial used to detect whether the sim has mutated since last full
        # invalidation — lets the tick timer skip work when nothing changed.
        self._last_map_serial: int = -1

    def _bind_map_buffer(self) -> None:
        """(Re)attach our ctypes view to the engine's current map pointer.

        Called at init and before each render. Micropolis normally allocates
        the buffer once, but future reload/clear features could move it.
        Without this guard we would read freed memory."""
        ptr = int(self.sim.getMapBuffer())
        if ptr != self._map_ptr:
            self._map_ptr = ptr
            self._map = (ctypes.c_uint16 * (WORLD_W * WORLD_H)).from_address(ptr)

    # --- rendering ------------------------------------------------------

    # Classes whose glyph cycles between two frames.
    _ANIM_GLYPH_CYCLE = {
        "water_shallow": ("~", "≈"),
        "water_deep":    ("≈", "~"),
        # Busy roads animate their straight segments only — directional
        # intersections stay stable so the road layout remains readable.
        "road_busy":     ("─", "•"),
        "flood":         ("≋", "≈"),
    }

    # Classes whose fg colour pulses between two values, simulating heat /
    # glow. Overrides the base style every other animation frame.
    _ANIM_STYLE_OVERRIDES = {
        "indus_low":  "rgb(180,60,60) on rgb(50,18,18)",
        "indus_mid":  "bold rgb(255,120,120) on rgb(60,22,22)",
        "indus_hi":   "bold rgb(255,160,160) on rgb(70,30,30)",
        "power":      "bold rgb(255,240,100) on rgb(35,30,10)",
        "nuclear":    "bold rgb(255,255,160) on rgb(65,58,18)",
        "plant":      "bold rgb(255,140,90) on rgb(65,25,20)",
        "fire":       "bold rgb(255,200,80) on rgb(80,35,15)",
        "rad":        "bold rgb(255,120,255) on rgb(55,18,55)",
    }

    def _alt_style(self, klass: str) -> Style:
        """Cached 'alternate-frame' style for a class. Used for pulses."""
        cached = self._pulse_styles.get(klass)
        if cached is None:
            base = self._styles.get(klass, self._unknown_style)
            override = self._ANIM_STYLE_OVERRIDES.get(klass)
            alt = Style.parse(override) if override else base
            self._pulse_styles[klass] = (base, alt)
            return alt
        return cached[1]

    def advance_animation(self) -> None:
        """Increment the frame counter and repaint all visible rows.
        Called by SimCityApp's 2 Hz animation timer."""
        self._anim_frame ^= 1  # toggle 0/1 — we only have two frames
        # Invalidate the whole viewport. Cheap: only ~40 rows re-render.
        self.refresh()

    def set_preview(self, footprint: tuple[int, int, int, int] | None) -> None:
        """Configure the tool-preview overlay. `footprint` is (w, h, dx, dy)
        relative to the cursor, or None to hide. The 'can_place' flag is
        computed here by scanning the footprint against the current map."""
        if footprint is None:
            if self._preview is not None:
                self._preview = None
                self.refresh()
            return
        w, h, dx, dy = footprint
        self._bind_map_buffer()
        cx, cy = self.cursor_x, self.cursor_y
        x0 = cx + dx
        y0 = cy + dy
        can_place = True
        # 1×1 tools (road/wire/rail/bulldoze) get skipped — we show the
        # preview on the single cursor cell but don't gate on class.
        if not (w == 1 and h == 1):
            for tx in range(x0, x0 + w):
                for ty in range(y0, y0 + h):
                    if not (0 <= tx < WORLD_W and 0 <= ty < WORLD_H):
                        can_place = False
                        break
                    klass = tiles._TABLE[
                        self._map[tx * WORLD_H + ty] & tiles.TILE_MASK
                    ][1]
                    if klass not in ("dirt", "grass"):
                        can_place = False
                        break
                if not can_place:
                    break
        self._preview = (w, h, dx, dy, can_place)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        """Called by Textual for every visible row; y is viewport-relative.

        Textual calls this once per visible row per paint pass. We detect
        the FIRST row of a pass (monotonic y resets to < last) and only
        then pay for the defensive map-buffer probe and the overlay byte
        fetch — reusing them for the remaining rows of the pass."""
        scroll_x, scroll_y = self.scroll_offset
        tile_y = y + int(scroll_y)
        width = self.size.width
        if tile_y < 0 or tile_y >= WORLD_H:
            return Strip.blank(width)

        # New-pass detection — cheap heuristic: if Textual asks for a row
        # whose index is ≤ the last we rendered, we're in a fresh pass.
        if y <= self._last_pass_row:
            self._bind_map_buffer()
            self._pass_overlay_cache = (
                overlay_buffer(self.sim, self.overlay_mode)
                if self.overlay_mode != "off"
                else None
            )
        self._last_pass_row = y

        # Only build segments for the visible x range — saves work proportional
        # to the hidden portion of the map.
        start_x = max(0, int(scroll_x))
        end_x = min(WORLD_W, start_x + width)

        table = tiles._TABLE
        styles = self._styles
        unknown = self._unknown_style
        mask = tiles.TILE_MASK
        m = self._map
        H = WORLD_H
        cx, cy = self.cursor_x, self.cursor_y
        cursor_style = self._cursor_style

        # Reuse the overlay buffer fetched at the start of this pass.
        overlay = self._pass_overlay_cache
        overlay_style = self._overlay_style_cache if overlay is not None else None

        segments: list[Segment] = []
        run_chars: list[str] = []
        run_style: Style | None = None
        frame = self._anim_frame
        glyph_cycle = self._ANIM_GLYPH_CYCLE
        pulse_classes = self._ANIM_STYLE_OVERRIDES
        # Cursor blink: on even frames use the bright style; on odd frames
        # fall back to a slightly dimmer variant so the yellow pulses.
        if frame == 1:
            cursor_now = Style.parse("bold rgb(40,40,0) on rgb(200,170,40)")
        else:
            cursor_now = cursor_style

        # Preview footprint — compute bounds once per row.
        preview = self._preview
        prev_x0 = prev_x1 = prev_y0 = prev_y1 = -1
        prev_style = None
        if preview is not None:
            w, h, dx, dy, can_place = preview
            prev_x0 = cx + dx
            prev_x1 = prev_x0 + w
            prev_y0 = cy + dy
            prev_y1 = prev_y0 + h
            prev_style = self._preview_ok if can_place else self._preview_bad

        # Rectangle-zoning preview — if an anchor is set, tint the rect
        # from anchor to cursor so the player sees the area they're about
        # to fill on right-click.
        rect_anchor = self._rect_anchor
        rect_x0 = rect_x1 = rect_y0 = rect_y1 = -1
        if rect_anchor is not None:
            ax, ay = rect_anchor
            rect_x0, rect_x1 = min(ax, cx), max(ax, cx) + 1
            rect_y0, rect_y1 = min(ay, cy), max(ay, cy) + 1
        for x in range(start_x, end_x):
            if overlay is not None:
                # 8× downsample: overlay cell at (x//8, tile_y//8) in a
                # 15-wide row-major byte grid.
                ox, oy = x // 8, tile_y // 8
                b = overlay[oy * 15 + ox] if 0 <= ox < 15 and 0 <= oy < 12 else 0
                glyph, _ = overlay_glyph_and_color(self.overlay_mode, b)
                style = cursor_now if (x == cx and tile_y == cy) else overlay_style
            else:
                glyph, klass = table[m[x * H + tile_y] & mask]
                # Animated 2-frame glyph swap for select classes.
                cycle = glyph_cycle.get(klass)
                if cycle is not None:
                    glyph = cycle[frame]
                else:
                    # Pattern cycling — zones and terrain alternate a pair
                    # of glyphs by position to avoid "RRRR" letter spam.
                    pattern = tiles._PATTERN.get(klass)
                    if pattern is not None:
                        glyph = pattern[(x + tile_y) & 1]
                        # Landmark accents — a ~2% sprinkle of iconic
                        # symbols on high-density zones, per Part 6.
                        lm = tiles._LANDMARK.get(klass)
                        if lm is not None and (x * 7 + tile_y * 13) % tiles._LANDMARK_PRIME == 0:
                            glyph = lm
                if x == cx and tile_y == cy:
                    style = cursor_now
                elif frame == 1 and klass in pulse_classes:
                    style = self._alt_style(klass)
                else:
                    style = styles.get(klass, unknown)
                # Preview overlay — tint footprint cells (excluding the
                # cursor itself, which already stands out).
                if (prev_style is not None
                        and prev_x0 <= x < prev_x1
                        and prev_y0 <= tile_y < prev_y1
                        and not (x == cx and tile_y == cy)):
                    style = style + prev_style
                # Rectangle-zoning tint — gentle amber overlay on the rect
                # from the anchor to the current cursor.
                if (rect_x0 <= x < rect_x1 and rect_y0 <= tile_y < rect_y1
                        and not (x == cx and tile_y == cy)):
                    style = style + Style.parse("on rgb(60,45,15)")
                # Anchor cell — highlight the left-click corner so the
                # player can confirm what's locked in.
                if rect_anchor is not None and (x, tile_y) == rect_anchor:
                    style = self._anchor_style
            if style is run_style:
                run_chars.append(glyph)
            else:
                if run_chars:
                    segments.append(Segment("".join(run_chars), run_style))
                run_chars = [glyph]
                run_style = style
        if run_chars:
            segments.append(Segment("".join(run_chars), run_style))

        visible_cols = end_x - start_x
        if visible_cols < width:
            # Pad with blanks when the map doesn't reach the right edge.
            segments.append(Segment(" " * (width - visible_cols)))
        return Strip(segments, width)

    # --- refresh / invalidation ----------------------------------------

    def set_overlay_mode(self, mode: str) -> None:
        """Switch overlay mode and re-cache the parsed style for that mode."""
        from .screens import _OVERLAY_COLORS
        self.overlay_mode = mode
        if mode == "off":
            self._overlay_style_cache = None
        else:
            self._overlay_style_cache = Style.parse(
                _OVERLAY_COLORS.get(mode, "white")
            )
        self.refresh()

    def refresh_all_tiles(self) -> None:
        """Mark the whole virtual map dirty (used after a sim tick if the
        engine's mapSerial has advanced, or after a tool application)."""
        # Sync the serial so the 1Hz timer doesn't immediately re-refresh.
        self._last_map_serial = self.sim.mapSerial
        self.refresh()

    def refresh_if_map_changed(self) -> bool:
        """Cheap check during the tick timer: only invalidate if the engine
        actually changed something. Returns True if a refresh was issued."""
        serial = self.sim.mapSerial
        if serial != self._last_map_serial:
            self._last_map_serial = serial
            self.refresh()
            return True
        return False

    def scroll_to_cursor(self) -> None:
        # Keep a small margin around the cursor so it isn't pinned to the edge.
        self.scroll_to_region(
            Region(self.cursor_x - 4, self.cursor_y - 2, 9, 5),
            animate=False,
            force=True,
        )

    def _refresh_row(self, tile_y: int) -> None:
        """Mark only a single tile row dirty (virtual coordinates)."""
        self.refresh(Region(0, tile_y, WORLD_W, 1))

    def watch_cursor_x(self, old: int, new: int) -> None:
        # Reactive watchers fire on *first access* too, which for a
        # --headless agent-API read happens before the widget ever
        # mounts (no App context). Refresh / scroll methods need a live
        # app; just skip the display update when there's nothing to
        # display to.
        if not self.is_mounted:
            return
        # old and new cursor are on the same y, so one row repaint covers both.
        self._refresh_row(self.cursor_y)
        self.scroll_to_cursor()

    def watch_cursor_y(self, old: int, new: int) -> None:
        if not self.is_mounted:
            return
        self._refresh_row(old)
        self._refresh_row(new)
        self.scroll_to_cursor()

    # --- mouse ----------------------------------------------------------

    def _event_to_tile(self, event: events.MouseEvent) -> tuple[int, int] | None:
        """Convert a widget-local mouse event to a tile coordinate, respecting
        the current scroll offset. Returns None if outside the world."""
        tx = event.x + int(self.scroll_offset.x)
        ty = event.y + int(self.scroll_offset.y)
        if 0 <= tx < WORLD_W and 0 <= ty < WORLD_H:
            return (tx, ty)
        return None

    # --- two-click rectangle ------------------------------------------
    # Left-click sets an anchor and applies the tool at that tile (classic
    # single-click behaviour preserved). Right-click, if an anchor is set,
    # fills the rectangle from the anchor to the right-click position
    # with the selected tool. Great for zoning large districts.

    def set_rect_anchor(self, spot: tuple[int, int] | None) -> None:
        """Configure the anchor marker rendered on the map. None clears it."""
        self._rect_anchor = spot
        self.refresh()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        spot = self._event_to_tile(event)
        if spot is None:
            return
        # Button 1 = left, 3 = right (Textual convention).
        if event.button == 3:
            # Right-click: if an anchor is set, fill the rect and clear.
            anchor = getattr(self, "_rect_anchor", None)
            if anchor is not None:
                self.cursor_x, self.cursor_y = spot
                self.post_message(self.RectApply(*anchor, *spot))
                self.set_rect_anchor(None)
            return
        # Left-click — apply tool at point AND set anchor for possible rect.
        self.capture_mouse()
        self.cursor_x, self.cursor_y = spot
        self._drag_last = spot
        self.set_rect_anchor(spot)
        self.post_message(self.ToolApply(*spot, *spot))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_last is None:
            return
        spot = self._event_to_tile(event)
        if spot is None or spot == self._drag_last:
            return
        self.cursor_x, self.cursor_y = spot
        x1, y1 = self._drag_last
        x2, y2 = spot
        self.post_message(self.ToolApply(x1, y1, x2, y2))
        self._drag_last = spot

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag_last is not None:
            self._drag_last = None
            self.release_mouse()


class StatusPanel(Static):
    """City Status — RCI demand bars, pollution/crime/fire risk."""

    def __init__(self, sim) -> None:
        super().__init__()
        self.sim = sim
        self.border_title = "CITY STATUS"
        # Memoise the last snapshot — the tick timer calls refresh_panel() at
        # 10 Hz and the panel redraws were causing visible flicker. We only
        # rebuild/update when an input has actually changed.
        self._last_snapshot: tuple | None = None
        # Micropolis's census counter sweeps the map incrementally: resPop
        # / comPop / indPop drop to 0 at the start of each ~48-tick cycle
        # and climb back up to the true value. Without smoothing, the bars
        # visibly shrink and grow every few seconds. We hold a rolling max
        # over a window larger than one full cycle so the displayed value
        # tracks the peak — the true count — and only drops if multiple
        # cycles agree on a lower number (i.e. the city really shrank).
        from collections import deque
        self._r_window: deque[int] = deque(maxlen=80)
        self._c_window: deque[int] = deque(maxlen=80)
        self._i_window: deque[int] = deque(maxlen=80)

    # Filled = solid block in the series' colour; empty = light shade in a
    # dim grey so the track stays visible and the bar reads like a gauge
    # instead of a dotted line.
    _FILLED = "█"
    _EMPTY = "░"

    def _bar(self, value: int, max_value: int = 250, width: int = 12) -> Text:
        n = max(0, min(width, int((value / max(max_value, 1)) * width)))
        t = Text()
        t.append(self._FILLED * n, style="bold")
        t.append(self._EMPTY * (width - n), style="rgb(70,70,70)")
        return t

    def refresh_panel(self) -> None:
        s = self.sim
        # Feed the sliding windows — the max of each window is what we show.
        self._r_window.append(s.resPop)
        self._c_window.append(s.comPop)
        self._i_window.append(s.indPop)
        res_stable = max(self._r_window)
        com_stable = max(self._c_window)
        ind_stable = max(self._i_window)

        # Round averages to nearest 5 so tiny sub-tick jitter doesn't retrigger
        # the repaint (traffic/pollution/crime update continuously).
        traffic = round(s.trafficAverage / 5) * 5
        pollution = round(s.pollutionAverage / 5) * 5
        crime = round(s.crimeAverage / 5) * 5

        snapshot = (
            res_stable, com_stable, ind_stable,
            traffic, pollution, crime,
            max(s.cityPop, 0),
        )
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot

        # Use a stable, fixed denominator for R/C/I so the bars don't jump
        # around when the mix rebalances month to month. 600 corresponds
        # roughly to a "thriving city" ceiling — past that, the bar pegs.
        t = Text()
        t.append("Residential  ", style="bold green")
        t.append_text(self._bar(res_stable, 600))
        t.append("\n")
        t.append("Commercial   ", style="bold cyan")
        t.append_text(self._bar(com_stable, 600))
        t.append("\n")
        t.append("Industrial   ", style="bold yellow")
        t.append_text(self._bar(ind_stable, 600))
        t.append("\n\n")
        t.append("Traffic      ")
        t.append_text(self._bar(traffic, 250))
        t.append("\n")
        t.append("Pollution    ")
        t.append_text(self._bar(pollution, 250))
        t.append("\n")
        t.append("Crime        ")
        t.append_text(self._bar(crime, 250))
        t.append("\n\n")
        t.append(f"Zones R/C/I  {res_stable:>4d} / {com_stable:>3d} / {ind_stable:>3d}\n")
        t.append(f"Total Pop    {snapshot[-1]:,}\n")
        self.update(t)


class BudgetPanel(Static):
    def __init__(self, sim) -> None:
        super().__init__()
        self.sim = sim
        self.border_title = "BUDGET"
        self._last_snapshot: tuple | None = None

    def refresh_panel(self) -> None:
        s = self.sim
        snapshot = (
            s.totalFunds, s.cityTax,
            round(s.roadPercent, 2), round(s.policePercent, 2),
            round(s.firePercent, 2), s.cashFlow,
        )
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        # Lines kept ≤ 24 chars so the panel survives a narrow side column
        # (minimum ~32 chars of content area after borders and padding).
        t = Text()
        t.append(f"Balance    ${s.totalFunds:>10,}\n", style="bold green")
        t.append(f"Cash flow  ${s.cashFlow:>+10,}\n")
        t.append(f"Tax rate          {s.cityTax:>3d}%\n")
        t.append(f"Road fund         {int(s.roadPercent*100):>3d}%\n")
        t.append(f"Police fund       {int(s.policePercent*100):>3d}%\n")
        t.append(f"Fire fund         {int(s.firePercent*100):>3d}%\n")
        self.update(t)


class ToolsPanel(Static):
    """Clickable tool list — each tool is one line, click to select."""

    class Selected(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "TOOLS"
        self.selected: int = 0  # index into TOOLS

    def on_click(self, event: events.Click) -> None:
        # event.y is widget-relative, with 0 being the first rendered line.
        # Our refresh_panel() renders tools on lines 0..len(TOOLS)-1, then
        # a blank line and three hint lines below. Only forward clicks that
        # land on a tool row.
        idx = event.y
        if 0 <= idx < len(TOOLS):
            self.post_message(self.Selected(idx))

    def refresh_panel(self) -> None:
        # Build a Text line-by-line so the sample glyph can carry its own
        # rich style (fg + bg), independent of the label.
        t = Text()
        for i, tool in enumerate(TOOLS):
            prefix = "▶ " if i == self.selected else "  "
            t.append(prefix + tool.key + " ",
                     style="bold reverse" if i == self.selected else "")
            t.append(tool.glyph, style=tool.style or None)
            # Strip the dollar hint from the legacy label since cost
            # appears in its own column now.
            short_label = tool.label.split(" ($")[0]
            t.append(f" {short_label:<18}",
                     style="bold" if i == self.selected else "")
            t.append(f"${tool.cost:>5d}", style="dim yellow")
            t.append("\n")
        t.append("\n")
        t.append_text(Text.from_markup(
            "[dim]arrows move · enter apply · click to anchor · right-click: fill rect[/]\n"
            "[dim]mouse drag: line  ·  l legend  ·  s save  ·  L load  ·  A advisor[/]\n"
            "[dim]t tutorial  b budget  g graphs  e eval  o overlay[/]\n"
            "[dim]? help  ·  p pause  ·  q quit[/]"
        ))
        self.update(t)


class SimCityApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "SimCity — Terminal"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("b", "budget", "Budget"),
        Binding("g", "graphs", "Graphs"),
        Binding("e", "evaluate", "Eval"),
        Binding("o", "cycle_overlay", "Overlay"),
        Binding("t", "tutorial", "Tutorial"),
        Binding("s", "save", "Save"),
        Binding("l", "legend", "Legend"),
        Binding("L", "load", "Load"),
        Binding("A", "advisor", "Advisor"),
        Binding("escape", "cancel_anchor", "Cancel", show=False),
        Binding("question_mark", "help", "Help"),
        # priority=True so arrow keys + enter aren't eaten by the scrollable
        # MapView (which has default bindings for scroll_up / scroll_down).
        Binding("enter", "apply_tool", "Apply", priority=True),
        Binding("space", "apply_tool", "Apply", show=False, priority=True),
        Binding("up",    "move_cursor(0,-1)", "↑", show=False, priority=True),
        Binding("down",  "move_cursor(0,1)",  "↓", show=False, priority=True),
        Binding("left",  "move_cursor(-1,0)", "←", show=False, priority=True),
        Binding("right", "move_cursor(1,0)",  "→", show=False, priority=True),
        # Each tool binds to its own single-char key (digits for 1–9, letters
        # for advanced tools).
        *[Binding(tool.key, f"select_tool({i})", show=False)
          for i, tool in enumerate(TOOLS)],
    ]

    paused: reactive[bool] = reactive(False)

    def __init__(self, city: str = "haight", *, agent_port: int | None = None,
                 sound: bool = False, music: bool = False) -> None:
        # NB: library defaults are OFF for both sound and music so that
        # tests (which instantiate SimCityApp() directly) don't spawn
        # audio subprocesses. The CLI's `run()` below flips both to ON
        # by default — that's the actual user-facing default.
        super().__init__()
        self._agent_port = agent_port
        self.sounds = SoundBoard(enabled=sound)
        self.music = MusicPlayer(enabled=music)
        self.sim = new_sim(city)
        self.map_view = MapView(self.sim)
        self.status_panel = StatusPanel(self.sim)
        self.budget_panel = BudgetPanel(self.sim)
        self.tools_panel = ToolsPanel()
        # Cap the log to prevent unbounded memory growth in long sessions —
        # 500 lines is roughly 5-10 game years of events, plenty of scrollback.
        self.message_log = RichLog(
            id="log", highlight=False, markup=True, wrap=False, max_lines=500,
        )
        self.message_log.border_title = "MESSAGE LOG"
        # Track the last logged message for duplicate collapse ("…×N").
        self._last_log_text: str = ""
        self._last_log_count: int = 0
        # A one-line status strip immediately below the map, used for
        # transient action feedback (tool-apply results, rejections) that
        # should NOT clutter the persistent message log.
        self.flash_bar = Static(" ", id="flash-bar")
        self._flash_timer = None
        self._last_month = -1
        # Rolling stats history for the graphs screen — sampled on month change.
        self._history: list[dict] = []
        self._hook_messages()

    def _hook_messages(self) -> None:
        """Catch engine messages and route interesting ones to the log."""
        sim = self.sim

        # Map the subset of Micropolis UI callbacks we surface to log levels.
        # Silent ones (UIShowPicture, UIBudget, etc.) fall through.
        named_routes: dict[str, tuple[str, str]] = {
            "UIDidLoadCity":     ("success",  "Loaded [bold]{name}[/]"),
            "UIDidLoadScenario": ("news",     "Scenario: [bold]{name}[/]"),
            "UIDidSaveCity":     ("success",  "Saved city to disk."),
            "UIFire":            ("disaster", "Fire reported!"),
            "UIEarthquake":      ("disaster", "Earthquake!"),
            "UITornado":         ("disaster", "Tornado spotted!"),
            "UIMonster":         ("disaster", "Monster attack!"),
            "UIFlood":           ("disaster", "Flooding reported."),
            "UIMeltdown":        ("disaster", "Nuclear meltdown — evacuate!"),
            "UIPlane":           ("warn",     "Plane crash."),
            "UIExplosion":       ("warn",     "Explosion detected."),
            "UIShipWreck":       ("warn",     "Shipwreck at the harbour."),
            "UITrainWreck":      ("warn",     "Train wreck on the rail line."),
        }

        def cb(micropolis, name, *params):
            route = named_routes.get(name)
            if route is None:
                return
            level, template = route
            self.log_msg(template.format(name=sim.cityName or "city"), level=level)

        sim.callback = cb

    # --- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="map-col"):
                yield self.map_view
                yield self.flash_bar
                yield self.message_log
            with Vertical(id="side"):
                yield self.status_panel
                yield self.budget_panel
                yield self.tools_panel
        yield Footer()

    # --- lifecycle -------------------------------------------------------

    async def on_mount(self) -> None:
        self.map_view.border_title = (
            f"{self.sim.cityName or 'MegaCity One'}  —  "
            f"{WORLD_W}×{WORLD_H}"
        )
        self.map_view.refresh_all_tiles()
        self.map_view.scroll_to_cursor()
        self.status_panel.refresh_panel()
        self.budget_panel.refresh_panel()
        self.tools_panel.refresh_panel()
        self.log_msg("Welcome to MegaCity One.", level="success")
        self.log_msg("New? Press [bold]t[/] for tutorial or [bold]?[/] for keys.",
                     level="info")
        # Seed the flash bar with the starting tile info.
        self._show_hover_info(self.map_view.cursor_x, self.map_view.cursor_y,
                              force=True)
        # And the preview overlay for whatever tool is selected at startup.
        self._update_preview()
        self.set_interval(0.1, self.tick)
        self.set_interval(1.0, self.redraw_map)
        # 2 Hz animation driver — water ripples, cursor blink, heat pulse,
        # traffic flow on busy roads. Keeps each frame cheap (~2 ms for 40
        # visible rows) so the game stays snappy.
        self.set_interval(0.5, self.map_view.advance_animation)
        # Start the chiptune loop (if enabled + supported by the
        # system audio pipeline). Silent no-op otherwise.
        self.music.start()
        # Start the agent API on-demand (set via --agent-port CLI flag).
        self._agent_runner = None
        if self._agent_port is not None:
            from .agent_api import start_server
            self._agent_runner = await start_server(self, port=self._agent_port)
            self.log_msg(
                f"[cyan]agent API on http://127.0.0.1:{self._agent_port}[/]"
            )

    def tick(self) -> None:
        if self.paused:
            return
        self.sim.simTick()
        self.status_panel.refresh_panel()
        self.budget_panel.refresh_panel()
        self.update_header()
        if self.sim.cityMonth != self._last_month:
            # Detect year rollover (Dec → Jan). Chime + a log note so the
            # player sees a visible year marker.
            if self._last_month == 11 and self.sim.cityMonth == 0:
                self.sounds.play("chime")
                self.log_msg(
                    f"New year — [bold]{self.sim.cityYear}[/]. "
                    f"Pop {max(self.sim.cityPop, 0):,}  "
                    f"Score {self.sim.cityScore}",
                    level="news",
                )
                # Autosave every 5 game years to a rotating 3-slot buffer,
                # so a crash at most loses ~5 years of play.
                if self.sim.cityYear % 5 == 0:
                    self._autosave()
            self._last_month = self.sim.cityMonth
            self._sample_history()

    def _autosave(self) -> None:
        """Write a rotating autosave. Three slots (0/1/2) cycle on year %.
        Intentionally silent unless it fails — don't spam the log."""
        from .screens import SAVE_DIR
        try:
            SAVE_DIR.mkdir(parents=True, exist_ok=True)
            slot = (self.sim.cityYear // 5) % 3
            path = SAVE_DIR / f"autosave-{slot}.cty"
            self.sim.saveCityAs(str(path))
            if path.exists() and path.stat().st_size > 1000:
                self.flash_status(
                    f"[dim]💾 autosave → {path.name}[/]", seconds=1.0
                )
            else:
                self.log_msg(f"autosave failed at {path.name}", level="warn")
        except Exception as e:
            self.log_msg(f"autosave error: {e}", level="error")

    def _sample_history(self) -> None:
        """Snapshot the sim once per month for the graphs screen."""
        s = self.sim
        self._history.append({
            "year": s.cityYear, "month": s.cityMonth,
            "cityPop": max(s.cityPop, 0),
            "totalFunds": s.totalFunds,
            "resPop": s.resPop, "comPop": s.comPop, "indPop": s.indPop,
            "cityScore": s.cityScore,
        })
        # Cap history so a long play session doesn't unbounded-grow memory.
        if len(self._history) > 600:  # ~50 years at 1 sample/month
            self._history = self._history[-600:]

    def redraw_map(self) -> None:
        # Only invalidate if Micropolis actually mutated tiles; most 1-second
        # ticks don't change anything visible (no tool applied, no growth).
        self.map_view.refresh_if_map_changed()

    def update_header(self) -> None:
        s = self.sim
        month = _MONTH_NAMES[s.cityMonth % 12]
        paused = " · ⏸ PAUSED" if self.paused else ""
        self.sub_title = (
            f"{month} {s.cityYear}  ·  ${s.totalFunds:,}  ·  "
            f"Pop {max(s.cityPop, 0):,}{paused}"
        )
        # Keep the map border title responsive to cursor position so the
        # player can always see what they're hovering over.
        cx, cy = self.map_view.cursor_x, self.map_view.cursor_y
        klass = tiles._TABLE[self.map_view._map[cx * WORLD_H + cy] & tiles.TILE_MASK][1]
        self.map_view.border_title = (
            f"{s.cityName or 'MegaCity One'}  ·  "
            f"cursor ({cx},{cy}) [{klass}]"
        )

    # --- actions ---------------------------------------------------------

    # Severity → (icon, color) lookup for the message log.
    #
    # Per design doc Part 5 ("Emoji Usage Strategy"): emoji are used only
    # as signal highlights in the LOG (never in the tile grid). Icons here
    # get a trailing space so double-width emoji don't misalign the
    # surrounding text. The "ℹ" / "✓" / "✗" / "$" / "◉" rows are Unicode
    # symbols (1-cell) and don't need padding.
    _LOG_LEVELS = {
        "info":     ("ℹ ",   "cyan"),
        "success":  ("✓ ",   "green"),
        "warn":     ("⚠️ ",   "yellow"),
        "error":    ("✗ ",   "red"),
        "money":    ("💰",   "yellow"),
        "power":    ("⚡",   "yellow"),
        "disaster": ("🔥",   "bold red"),
        "news":     ("📰",   "magenta"),
    }

    def log_msg(self, msg: str, level: str = "info") -> None:
        """Persistent entry in the message log. Engine events, tutorial
        hints, year rollovers, disaster alerts, AI advisor notes. Use the
        `level` argument to pick an icon + colour.

        NOT for per-tool-action confirmations — those go to flash_status()."""
        s = self.sim
        stamp = f"[dim][{_MONTH_NAMES[s.cityMonth % 12]} {s.cityYear}][/]"
        icon, color = self._LOG_LEVELS.get(level, self._LOG_LEVELS["info"])
        # Icon already carries any needed trailing space — see _LOG_LEVELS.
        line = f"{stamp} [bold {color}]{icon}[/] {msg}"
        # Duplicate collapse — if the previous message was identical, edit
        # the last line in-place with a count suffix instead of adding a
        # new one. Matches "Collapse duplicates" in the design doc.
        if msg == self._last_log_text and self._last_log_count >= 1:
            self._last_log_count += 1
            # RichLog's .lines is an internal buffer of Strip — the cleanest
            # API to revise it is remove-last + add-new.
            try:
                self.message_log.lines.pop()
            except IndexError:
                pass
            self.message_log.write(f"{line} [dim]×{self._last_log_count}[/]")
        else:
            self._last_log_text = msg
            self._last_log_count = 1
            self.message_log.write(line)

    def flash_status(self, msg: str, seconds: float = 1.5) -> None:
        """Show a transient one-line message in the strip below the map.
        Auto-clears after `seconds` back to whatever hover info should
        show. Used for tool-apply confirmation, rejections, and other
        ephemeral feedback that shouldn't clutter the log."""
        self.flash_bar.update(Text.from_markup(msg))
        if self._flash_timer is not None:
            self._flash_timer.stop()

        def _clear():
            self._flash_timer = None
            # Fall back to the hover info at the cursor's current tile.
            self._show_hover_info(
                self.map_view.cursor_x, self.map_view.cursor_y,
                force=True,
            )

        self._flash_timer = self.set_timer(seconds, _clear)

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.flash_status("[yellow]⏸ paused[/]" if self.paused else "[green]▶ resumed[/]")
        self.update_header()

    def action_move_cursor(self, dx: str, dy: str) -> None:
        cx = max(0, min(WORLD_W - 1, self.map_view.cursor_x + int(dx)))
        cy = max(0, min(WORLD_H - 1, self.map_view.cursor_y + int(dy)))
        self.map_view.cursor_x = cx
        self.map_view.cursor_y = cy
        self.update_header()
        self._show_hover_info(cx, cy)
        self._update_preview()

    def _show_hover_info(self, x: int, y: int, force: bool = False) -> None:
        """Surface tile info in the flash bar while the player is navigating.
        Suppressed while a more important transient message is on screen —
        caller can pass force=True to bypass (used when that message just
        finished auto-clearing)."""
        if not force and self._flash_timer is not None:
            # A tool-result flash is still visible — don't overwrite it.
            return
        self.map_view._bind_map_buffer()
        tid = self.map_view._map[x * WORLD_H + y] & tiles.TILE_MASK
        glyph, klass = tiles._TABLE[tid]
        style = tiles.style_for(klass)
        # Current overlay value, if any.
        extra = ""
        mode = self.map_view.overlay_mode
        if mode != "off":
            from .screens import overlay_buffer
            buf = overlay_buffer(self.sim, mode)
            if buf is not None:
                ox, oy = x // 8, y // 8
                if 0 <= ox < 15 and 0 <= oy < 12:
                    val = buf[oy * 15 + ox]
                    extra = f"   {mode}: [bold]{val}[/]"
        self.flash_bar.update(Text.from_markup(
            f"[{style}] {glyph} [/]  ({x},{y})  [bold]{klass}[/]{extra}"
        ))

    def action_select_tool(self, idx: str) -> None:
        i = int(idx)
        if 0 <= i < len(TOOLS):
            self.tools_panel.selected = i
            self.tools_panel.refresh_panel()
            self.flash_status(f"Tool: [bold]{TOOLS[i].label}[/]")
            self.sounds.play("click")
            self._update_preview()
            # Changing tools cancels any pending rect anchor — its footprint
            # might not match the new tool anyway.
            self.map_view.set_rect_anchor(None)

    def action_cancel_anchor(self) -> None:
        """Escape key: clear any pending rect-zoning anchor."""
        if self.map_view._rect_anchor is not None:
            self.map_view.set_rect_anchor(None)
            self.flash_status("[dim]anchor cleared[/]", seconds=0.6)

    def _update_preview(self) -> None:
        """Push the footprint of the currently-selected tool to MapView."""
        tool = TOOLS[self.tools_panel.selected]
        fp = _TOOL_FOOTPRINT.get(tool.code)
        self.map_view.set_preview(fp)

    def action_apply_tool(self) -> None:
        tool = TOOLS[self.tools_panel.selected]
        cx, cy = self.map_view.cursor_x, self.map_view.cursor_y
        result = self.sim.doTool(tool.code, cx, cy)
        if result == me.TOOLRESULT_OK:
            self.flash_status(f"[green]✓ {tool.label}[/] @ ({cx},{cy})")
            self.sounds.play("bulldoze" if tool.code == me.TOOL_BULLDOZER else "build")
        elif result == me.TOOLRESULT_NO_MONEY:
            self.flash_status(f"[red]✗ not enough funds[/] for {tool.label}")
            self.sounds.play("deny")
        elif result == me.TOOLRESULT_NEED_BULLDOZE:
            self.flash_status("[red]✗ need to bulldoze this tile first[/]")
            self.sounds.play("deny")
        else:
            # TOOLRESULT_FAILED — the engine doesn't tell us why. Inspect the
            # tile under the cursor to infer a helpful reason.
            self.flash_status(_why_failed(tool, self.map_view, cx, cy))
            self.sounds.play("deny")
        self.map_view.refresh_all_tiles()
        self.budget_panel.refresh_panel()

    # --- modal / overlay actions ----------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_tutorial(self) -> None:
        self.push_screen(TutorialScreen())

    def action_legend(self) -> None:
        self.push_screen(LegendScreen())

    def action_advisor(self) -> None:
        # Pull the same snapshot the agent API exposes, so the advisor
        # sees exactly what a remote AI player would.
        from .agent_api import state_snapshot
        self.push_screen(AdvisorScreen(state_snapshot(self)))

    def action_save(self) -> None:
        def _after(result) -> None:
            if result is None:
                return
            ok, path = result
            if ok:
                self.flash_status(f"[green]✓ saved to {Path(path).name}[/]")
            else:
                self.flash_status("[red]✗ save failed[/]")
        self.push_screen(SaveScreen(self.sim), _after)

    def action_load(self) -> None:
        def _after(path: str | None) -> None:
            if not path:
                return
            # Micropolis's loadCity replaces the tile buffer in place, which
            # our defensive _bind_map_buffer picks up on the next render.
            ok = self.sim.loadCity(path)
            if ok:
                # Reset transient state — history belongs to the OLD city.
                self._history.clear()
                self._last_month = self.sim.cityMonth
                self.map_view.refresh_all_tiles()
                self.status_panel._last_snapshot = None
                self.budget_panel._last_snapshot = None
                self.status_panel.refresh_panel()
                self.budget_panel.refresh_panel()
                self.update_header()
                self.flash_status(f"[green]✓ loaded {Path(path).name}[/]")
            else:
                self.flash_status(f"[red]✗ failed to load {Path(path).name}[/]")
        self.push_screen(LoadScreen(), _after)

    def action_budget(self) -> None:
        self.push_screen(BudgetScreen(self.sim))

    def action_graphs(self) -> None:
        self.push_screen(GraphsScreen(self._history))

    def action_evaluate(self) -> None:
        self.push_screen(EvaluationScreen(self.sim))

    def action_cycle_overlay(self) -> None:
        modes = OVERLAY_MODES
        cur = self.map_view.overlay_mode
        idx = modes.index(cur) if cur in modes else 0
        new = modes[(idx + 1) % len(modes)]
        self.map_view.set_overlay_mode(new)
        label = "off" if new == "off" else new
        self.flash_status(f"Overlay: [bold]{label}[/]")

    def on_tools_panel_selected(self, message: ToolsPanel.Selected) -> None:
        """Relay clicks on a tool row into the select_tool action, so
        mouse and keyboard give identical results."""
        self.action_select_tool(str(message.index))

    def on_map_view_rect_apply(self, message: MapView.RectApply) -> None:
        """Fill the rectangle between the left-click anchor and the
        right-click corner with the selected tool. For 3×3 / 4×4 tools we
        step by the footprint width so zones don't overlap."""
        tool = TOOLS[self.tools_panel.selected]
        fp = _TOOL_FOOTPRINT.get(tool.code, (1, 1, 0, 0))
        w, h, _dx, _dy = fp
        step_x, step_y = max(1, w), max(1, h)
        x0, y0, x1, y1 = message.x1, message.y1, message.x2, message.y2
        ok = 0
        fail = 0
        no_money = 0
        for tx in range(x0, x1 + 1, step_x):
            for ty in range(y0, y1 + 1, step_y):
                if not (0 <= tx < WORLD_W and 0 <= ty < WORLD_H):
                    continue
                result = self.sim.doTool(tool.code, tx, ty)
                if result == me.TOOLRESULT_OK:
                    ok += 1
                elif result == me.TOOLRESULT_NO_MONEY:
                    no_money += 1
                    break  # abort early on broke — further calls will also fail
                else:
                    fail += 1
            if no_money:
                break
        self.map_view.refresh_all_tiles()
        self.budget_panel.refresh_panel()
        if no_money:
            self.flash_status(
                f"[red]✗ ran out of money — {ok} placed before stopping[/]"
            )
            self.sounds.play("deny")
        elif ok:
            self.flash_status(
                f"[green]✓ placed {ok}× {tool.label}[/] "
                f"[dim]({fail} failed)[/]"
            )
            self.sounds.play("build")
        else:
            self.flash_status(f"[red]✗ nothing could be placed in rectangle[/]")
            self.sounds.play("deny")

    # --- mouse handler ---------------------------------------------------

    def on_map_view_tool_apply(self, message: MapView.ToolApply) -> None:
        """Apply the selected tool at the clicked tile, or along a drag
        segment. We stay silent during drags to avoid flooding the log —
        only NO_MONEY is surfaced, since it halts progress."""
        tool = TOOLS[self.tools_panel.selected]
        if (message.x1, message.y1) == (message.x2, message.y2):
            result = self.sim.doTool(tool.code, message.x2, message.y2)
        else:
            result = self.sim.toolDrag(
                tool.code, message.x1, message.y1, message.x2, message.y2
            )
        if result == me.TOOLRESULT_NO_MONEY:
            self.flash_status(f"[red]✗ not enough funds[/] for {tool.label}")
        self.map_view.refresh_all_tiles()
        self.budget_panel.refresh_panel()


def run(city: str = "haight", *, agent_port: int | None = None,
        headless: bool = False, sound: bool = True,
        music: bool = True) -> None:
    if headless:
        # Headless mode: no TUI, just the agent API + sim ticking on an
        # asyncio loop. Useful for letting an AI agent play on its own.
        if agent_port is None:
            agent_port = 8787
        import asyncio as _asyncio
        from .agent_api import start_server
        app = SimCityApp(city, agent_port=agent_port)

        async def _headless_main() -> None:
            runner = await start_server(app, port=agent_port)
            print(f"[micropolis-tui] headless, agent API on "
                  f"http://127.0.0.1:{agent_port}")
            try:
                while True:
                    if not app.paused:
                        app.sim.simTick()
                        if app.sim.cityMonth != app._last_month:
                            app._last_month = app.sim.cityMonth
                            app._sample_history()
                    await _asyncio.sleep(0.1)
            finally:
                await runner.cleanup()

        try:
            _asyncio.run(_headless_main())
        except KeyboardInterrupt:
            pass
        return
    app = SimCityApp(city, agent_port=agent_port, sound=sound, music=music)
    try:
        app.run()
    finally:
        # Always stop the music loop subprocess on exit, even if the
        # Textual app crashed — otherwise aplay keeps going in the
        # background after the terminal returns.
        try:
            app.music.stop()
        except Exception:
            pass
        # Belt-and-suspenders: some terminals (esp. over SSH) keep mouse
        # tracking on after Textual exits, leaking sequences like
        # "35;92;24M…" into the shell. Force-disable all mouse modes and
        # show the cursor.
        import sys
        sys.stdout.write(
            "\033[?1000l"  # disable basic mouse tracking
            "\033[?1002l"  # disable button-event tracking
            "\033[?1003l"  # disable any-event (motion) tracking
            "\033[?1006l"  # disable SGR extended mouse mode
            "\033[?1015l"  # disable urxvt extended mouse mode
            "\033[?25h"    # show cursor
        )
        sys.stdout.flush()
