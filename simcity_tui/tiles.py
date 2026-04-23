"""Tile ID → (glyph, color) lookup.

Micropolis tile IDs are 0–1023 (the low 10 bits of the tile word). Most tiles
are bucketed into a category (for rendering cost and legibility); specific IDs
where the visual matters — road intersections, power-line directions, zone
density — have hand-crafted overrides.
"""

from __future__ import annotations

# (lo, hi inclusive, glyph, css class)
#
# The zone ranges are split into low/mid/high density sub-buckets using shade
# characters so the map tells you at a glance where the action is. The
# residential/commercial/industrial splits are approximate — Micropolis uses
# 9-tile 3×3 zone blocks where the centre tile encodes population level and
# the surrounding 8 tiles cycle. Coarse bucketing is correct enough for a TUI.
_RANGES: list[tuple[int, int, str, str]] = [
    # Terrain — dim, low-priority. The player's eye should land on zones
    # and infrastructure first; the ground is just a canvas.
    (0, 0,     ".", "dirt"),
    (1, 1,     ",", "grass"),
    (2, 4,     "~", "water_shallow"),
    (5, 20,    "≈", "water_deep"),
    (21, 36,   "♣", "tree"),
    (37, 43,   "^", "forest"),
    (44, 47,   "▒", "rubble"),
    (48, 51,   "≋", "flood"),
    (52, 52,   "☢", "rad"),
    (53, 55,   ",", "grass"),
    (56, 63,   "*", "fire"),
    # Roads — most get the default horizontal glyph; specific IDs are
    # overridden below with box-drawing for intersections.
    (64, 206,  "─", "road"),
    (207, 207, ".", "dirt"),
    (208, 223, "=", "power"),
    (224, 238, "≡", "rail"),
    (239, 239, "─", "road"),
    # Zones render as 2-glyph density patterns (checkerboard) rather than
    # repeated letters — see _PATTERN below. The glyph we store here is
    # the fallback used when the pattern lookup fails; in practice every
    # zone class has a pattern entry.
    (240, 265, "░", "resid_low"),
    (266, 350, "▒", "resid_mid"),
    (351, 422, "▓", "resid_hi"),
    (423, 470, "▤", "comm_low"),
    (470, 550, "▥", "comm_mid"),
    (551, 611, "▩", "comm_hi"),
    (612, 640, "▒", "indus_low"),
    (641, 670, "▓", "indus_mid"),
    (671, 692, "█", "indus_hi"),
    (693, 708, "⚓", "harbor"),    # PORTBASE..LASTPORT — seaport
    (709, 744, "✈", "airport"),    # AIRPORTBASE..~744 — plane glyph
    (745, 760, "▣", "plant"),     # COALBASE..LASTPOWERPLANT — coal stack
    (761, 773, "♨", "fire_st"),   # FIRESTBASE(761)..769 + spillover — fire station
    (774, 778, "◉", "police"),    # sighted target — police badge proxy
    (779, 799, "◎", "stadium"),   # ring for the stadium bowl
    (800, 810, "◎", "stadium"),   # FULLSTADIUM + animations
    # Nuclear plant is the 4x4 block NUCLEARBASE(811)..LASTZONE(826) in
    # micropolis.h. The legacy range 828-916 incorrectly tagged bridges,
    # radar, fountains, industrial base, explosions, and smoke as
    # "nuclear" — which is why bulldoze animations briefly showed ☢.
    (811, 826, "☢", "nuclear"),   # the canonical radiation trefoil
    (827, 827, "⚡", "fire"),      # LIGHTNINGBOLT (disaster marker)
    (828, 831, "═", "road"),      # HBRDG — horizontal bridge
    (832, 839, "✈", "airport"),   # RADAR animation
    (840, 843, "◌", "park"),      # FOUNTAIN — park fountain animation
    (844, 851, "█", "indus_hi"),  # INDBASE2 / TELEBASE industrial
    (852, 859, "▣", "plant"),     # SMOKEBASE industrial smokestack
    (860, 867, "*", "fire"),      # TINYEXP — small explosion frames
    (868, 915, "?", "misc"),      # mixed animation/unused tiles
    (916, 931, "▣", "plant"),     # COALSMOKE1..4 — coal plant chimney anim
    (932, 947, "◎", "stadium"),   # FOOTBALLGAME1/2 stadium animations
    (948, 951, "║", "road"),      # VBRDG — vertical bridge
    (952, 955, "☢", "nuclear"),   # NUKESWIRL — nuclear swirl animation
    # CHURCH1BASE(956)..CHURCH7LAST(1018) — extended zone churches. They
    # occupy residential land so they render with the residential palette;
    # hi-density shading matches their "community landmark" prominence.
    (956, 1018, "▓", "resid_hi"),
    (1019, 1023, "?", "misc"),    # truly unused tail
]

# Road glyph keyed by "tile offset within a 16-tile block" (i.e. id - 64
# mod 16). This is NOT the same as Micropolis's 4-bit connection index —
# the engine uses a lookup table (RoadTable in src/connect.cpp) that is
# sparse, with ROADS and ROADS2 reused for multiple connection patterns.
#
# Order here follows the enum in src/micropolis.h (HBRIDGE, VBRIDGE,
# ROADS, ROADS2, ROADS3..ROADS10, INTERSECTION, HROADPOWER, VROADPOWER,
# BRWH). The 16-tile layout repeats at +16 per traffic level, so the
# same offset works for base, low-traffic, and high-traffic blocks.
_ROAD_GLYPH: dict[int, str] = {
    0:  "═",  # 64  HBRIDGE       — horizontal bridge
    1:  "║",  # 65  VBRIDGE       — vertical bridge
    2:  "─",  # 66  ROADS         — horizontal road
    3:  "│",  # 67  ROADS2        — vertical road
    4:  "╮",  # 68  ROADS3        — S+W elbow
    5:  "╯",  # 69  ROADS4        — N+W elbow
    6:  "╰",  # 70  ROADS5        — N+E elbow
    7:  "╭",  # 71  ROADS6        — S+E elbow
    8:  "┬",  # 72  ROADS7        — missing N (T pointing down)
    9:  "┤",  # 73  ROADS8        — missing E (T pointing left)
    10: "┴",  # 74  ROADS9        — missing S (T pointing up)
    11: "├",  # 75  ROADS10       — missing W (T pointing right)
    12: "┼",  # 76  INTERSECTION  — 4-way
    13: "┿",  # 77  HROADPOWER    — road + power crossover
    14: "╪",  # 78  VROADPOWER
    15: "═",  # 79  BRWH          — bridge variant; render as horizontal
}

# Which 16-block classifies the tile. Traffic-bearing roads get a different
# style so a congested city visibly glows yellow-ish.
def _road_class_for_id(tile_id: int) -> str:
    # 64–79 = base (no traffic), 80–143 = low traffic, 144–206 = high traffic.
    if tile_id >= 144:
        return "road_busy"
    return "road"


# 2-glyph density patterns per tile class. render_line picks
# pattern[(x+tile_y) & 1] so every cell alternates — the result is
# checkerboard texture instead of "RRRRR" letter spam. Per design-doc
# Part 6. Terrain also gets subtle variation so large empty regions
# don't read as flat colour blocks.

# Landmark accents — single-cell Unicode symbols that visually read as
# emoji-like icons without the double-width alignment problems real
# emoji have. Sparse (~2% of cells) via a prime-hash so you see an
# occasional icon as a pleasant interruption, not a repeating texture.
# Per design-doc Parts 5+6: emoji as signal, not structure.
_LANDMARK: dict[str, str] = {
    "resid_hi":  "⌂",   # house glyph
    "comm_hi":   "◈",   # lozenge — "business district"
    "indus_hi":  "⚙",   # gear — "factory"
}
_LANDMARK_PRIME = 47  # every ~47th eligible cell → one accent
_PATTERN: dict[str, tuple[str, str]] = {
    # Residential — scales from sparse dirt-with-huts to dense blocks.
    "resid_low":  (".", "░"),
    "resid_mid":  ("░", "▒"),
    "resid_hi":   ("▒", "▓"),
    # Commercial — quadrant-filled blocks give a "grid of buildings" feel.
    "comm_low":   ("▤", "▥"),
    "comm_mid":   ("▥", "▦"),
    "comm_hi":    ("▦", "▩"),
    # Industrial — heavier blocks, read as "factory mass".
    "indus_low":  ("▒", "▓"),
    "indus_mid":  ("▓", "█"),
    "indus_hi":   ("█", "▓"),
    # Terrain — a light breath of variation.
    "dirt":       (".", ","),
    "grass":      (",", "."),
    "tree":       ("♣", "^"),
    "forest":     ("^", "♣"),
}


# Specific overrides that the blanket road rule doesn't cover: bridges, road
# ± power/rail crossovers, power lines, flooding, etc.
_OVERRIDES: dict[int, tuple[str, str]] = {
    # Bridges — distinct visual from a regular road.
    64: ("═", "bridge"),
    65: ("║", "bridge"),
    # 4-way intersection — brighter so it pops against straight road runs.
    76: ("┼", "road_inter"),
    # Road + power crossover (single-direction; road wins visually).
    77: ("┿", "road_pwr"),
    78: ("╪", "road_pwr"),
    # Road + rail crossover (BRWH/BRWV in Micropolis).
    79: ("┼", "road_rail"),
    95: ("┼", "road_rail"),
    # Power lines — 16-tile block with similar layout to roads, but we only
    # hand-map the common ones; off-axis curves are rare in play.
    208: ("═", "power"),
    209: ("║", "power"),
    210: ("╮", "power"),
    211: ("╔", "power"),  # Micropolis LVPOWER is at 211; variant block below
    212: ("╝", "power"),
    213: ("╚", "power"),
    214: ("╦", "power"),
    215: ("╣", "power"),
    216: ("╩", "power"),
    217: ("╠", "power"),
    218: ("╬", "power"),
    # Rail-power crossovers
    221: ("═", "rail"),
    222: ("║", "rail"),
    # Rail
    224: ("═", "rail"),
    225: ("║", "rail"),
    # Flood / disaster animated frames.
    48: ("≋", "flood"),
    49: ("≋", "flood"),
    50: ("≋", "flood"),
    51: ("≋", "flood"),
}

# Foreground colors per category. Rich style strings. Keeping bold restrained
# so the map feels more like a painted map than a Christmas tree.
# Palette per the design doc's brightness budget: terrain dim, infrastructure
# medium, zones + civic bright. Industrial is red (per the SimCity legacy),
# commercial blue, residential green.
COLOR: dict[str, str] = {
    # Terrain — dim, low priority.
    "dirt":          "rgb(40,80,40)",
    "grass":         "rgb(50,110,50)",
    "water_shallow": "rgb(70,130,180)",
    "water_deep":    "rgb(40,80,140)",
    "tree":          "rgb(30,120,30)",
    "forest":        "rgb(20,90,20)",
    "rubble":        "rgb(130,70,50)",
    "flood":         "rgb(100,190,230)",
    "rad":           "bold rgb(230,80,230)",
    "fire":          "bold rgb(255,140,50)",
    # Infrastructure — mid priority, brightness encodes traffic.
    "road":          "rgb(180,180,120)",
    "road_inter":    "bold rgb(230,200,120)",   # 4-way intersections pop brighter
    "bridge":        "rgb(200,210,230)",
    "road_busy":     "bold rgb(230,200,120)",
    "road_pwr":      "bold rgb(255,220,80)",
    "road_rail":     "rgb(190,180,150)",
    "power":         "bold rgb(220,220,100)",   # Part 6 palette
    "rail":          "rgb(170,140,110)",
    # Zones — letter case + brightness carries density.
    "resid_low":     "rgb(80,200,120)",
    "resid_mid":     "bold rgb(80,200,120)",
    "resid_hi":      "bold rgb(120,240,150)",
    "comm_low":      "rgb(80,140,220)",
    "comm_mid":      "bold rgb(80,140,220)",
    "comm_hi":       "bold rgb(120,180,255)",
    "indus_low":     "rgb(220,90,90)",
    "indus_mid":     "bold rgb(220,90,90)",
    "indus_hi":      "bold rgb(255,120,120)",
    # Civic / industry — bright, highest priority.
    "airport":       "bold rgb(230,230,230)",
    "plant":         "bold rgb(230,90,90)",
    "fire_st":       "bold rgb(255,120,70)",
    "police":        "bold rgb(120,160,240)",
    "stadium":       "bold rgb(240,200,120)",
    "nuclear":       "bold rgb(250,250,120)",
    "harbor":        "bold rgb(180,200,240)",
    "park":          "bold rgb(100,220,140)",
    "misc":          "rgb(180,180,180)",
}

# Background colors per category — painted "scenery" behind the glyph. Kept
# dark and desaturated so the foreground glyphs stay readable and the map
# reads like a paper map, not a dashboard.
BG: dict[str, str] = {
    # Terrain — nearly black so the map reads like a painted canvas.
    "dirt":          "rgb(8,15,8)",
    "grass":         "rgb(12,22,12)",
    "water_shallow": "rgb(10,25,50)",
    "water_deep":    "rgb(5,15,35)",
    "tree":          "rgb(8,20,10)",
    "forest":        "rgb(5,15,6)",
    "rubble":        "rgb(40,20,12)",
    "flood":         "rgb(20,50,80)",
    "rad":           "rgb(40,10,40)",
    "fire":          "rgb(60,20,8)",
    # Infrastructure
    "road":          "rgb(22,22,18)",
    "road_inter":    "rgb(38,30,12)",
    "bridge":        "rgb(15,20,35)",
    "road_busy":     "rgb(38,30,12)",
    "road_pwr":      "rgb(32,28,10)",
    "road_rail":     "rgb(28,22,18)",
    "power":         "rgb(28,24,8)",
    "rail":          "rgb(25,20,15)",
    # Zones — red-industrial matches foreground tone now.
    "resid_low":     "rgb(10,28,15)",
    "resid_mid":     "rgb(12,38,18)",
    "resid_hi":      "rgb(15,48,22)",
    "comm_low":      "rgb(10,20,38)",
    "comm_mid":      "rgb(12,25,48)",
    "comm_hi":       "rgb(15,32,58)",
    "indus_low":     "rgb(40,15,15)",
    "indus_mid":     "rgb(50,18,18)",
    "indus_hi":      "rgb(60,22,22)",
    # Civic — a bit brighter bg so they pop as highest priority.
    "airport":       "rgb(35,35,40)",
    "plant":         "rgb(60,18,18)",
    "fire_st":       "rgb(60,25,15)",
    "police":        "rgb(18,28,55)",
    "stadium":       "rgb(50,35,15)",
    "nuclear":       "rgb(55,50,15)",
    "harbor":        "rgb(20,30,50)",
    "park":          "rgb(12,32,18)",
    "misc":          "rgb(25,25,25)",
}

_TABLE: list[tuple[str, str]] = [(" ", "dirt")] * 1024


def _build() -> None:
    for lo, hi, glyph, klass in _RANGES:
        for i in range(lo, min(hi + 1, 1024)):
            _TABLE[i] = (glyph, klass)
    # Programmatic road glyphs for the entire 64–206 range. We apply this
    # BEFORE _OVERRIDES so hand-crafted entries (bridges, crossovers) win.
    for tid in range(64, 207):
        glyph = _ROAD_GLYPH[(tid - 64) % 16]
        _TABLE[tid] = (glyph, _road_class_for_id(tid))
    for i, (g, k) in _OVERRIDES.items():
        if 0 <= i < 1024:
            _TABLE[i] = (g, k)


_build()

TILE_MASK = 0x03FF  # low 10 bits are the tile ID


# Zone-family grouping — used by the map renderer to draw a colored
# perimeter around contiguous blocks of the same family, so a 3×3 zone
# (or a wider district of adjacent zones) reads as one unified unit
# rather than a pile of shaded cells.
ZONE_FAMILY: dict[str, str] = {
    "resid_low": "resid", "resid_mid": "resid", "resid_hi": "resid",
    "comm_low":  "comm",  "comm_mid":  "comm",  "comm_hi":  "comm",
    "indus_low": "indus", "indus_mid": "indus", "indus_hi": "indus",
}


def style_for(klass: str) -> str:
    """Compose fg + bg into a Rich style string."""
    fg = COLOR.get(klass, "rgb(255,0,255)")
    bg = BG.get(klass, "rgb(0,0,0)")
    return f"{fg} on {bg}"


def render(tile_word: int) -> str:
    """Return a rich-markup string for one tile, including color."""
    tid = tile_word & TILE_MASK
    glyph, klass = _TABLE[tid]
    return f"[{style_for(klass)}]{glyph}[/]"


def glyph_and_class(tile_word: int) -> tuple[str, str]:
    return _TABLE[tile_word & TILE_MASK]
