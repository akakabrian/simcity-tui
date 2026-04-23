"""Modal screens: help, budget, graphs, evaluation, and overlay key cycle.

These are dialogs the player opens with a single keypress and dismisses with
`escape`. They read from the live sim so what you see is always current.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .engine import WORLD_H, WORLD_W, CITIES_DIR


SAVE_DIR = Path.home() / ".local" / "share" / "simcity-tui" / "saves"


# ---------- shared helpers ----------------------------------------------------


def _sparkline(values: list[float], width: int = 40) -> str:
    """Unicode block sparkline. Scales to the min/max of the window."""
    if not values:
        return " " * width
    if len(values) > width:
        # Decimate: pick evenly-spaced samples.
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        idx = int(((v - lo) / span) * (len(blocks) - 1))
        out.append(blocks[max(0, min(len(blocks) - 1, idx))])
    return "".join(out).ljust(width)


def _tall_chart(values: list[float], width: int, height: int,
                style: str = "bold green") -> Text:
    """Render a vertical column bar chart using 1/8-cell block characters.

    Height is in terminal rows; each row contributes 8 sub-levels via
    `▁▂▃▄▅▆▇█`, so effective vertical resolution is height*8 levels.
    Returns a Text object rendered top-to-bottom, left-to-right."""
    t = Text()
    if not values or width < 1 or height < 1:
        return t
    # Decimate / pad to exactly `width` columns.
    if len(values) > width:
        step = len(values) / width
        series = [values[int(i * step)] for i in range(width)]
    else:
        pad = [0] * (width - len(values))
        series = pad + list(values)
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1
    # Each value becomes an integer 0..height*8 number of sub-cells filled.
    levels = [
        max(0, min(height * 8, int(round(((v - lo) / span) * height * 8))))
        for v in series
    ]
    blocks = " ▁▂▃▄▅▆▇█"
    for row_from_top in range(height):
        # Bottom rows draw first (row 0 is top, row height-1 is bottom).
        row_bottom = height - 1 - row_from_top
        min_sub = row_bottom * 8
        for col, n_sub in enumerate(levels):
            rem = n_sub - min_sub
            if rem <= 0:
                t.append(" ")
            elif rem >= 8:
                t.append(blocks[8], style=style)
            else:
                t.append(blocks[rem], style=style)
        t.append("\n")
    return t


def _bar(value: int, max_value: int, width: int = 20) -> str:
    n = max(0, min(width, int((value / max(max_value, 1)) * width)))
    return "█" * n + "·" * (width - n)


# ---------- help --------------------------------------------------------------


HELP_TEXT = """[bold]SIMCITY — TERMINAL[/bold]

[bold #f0c080]MOVEMENT[/]
  ↑ ↓ ← →             move cursor
  mouse click         jump cursor and apply tool
  mouse drag          draw with selected tool (roads, wire, zones)

[bold #f0c080]TOOLS[/] — press the key to select, enter (or click) to apply
  1  Residential     2  Commercial        3  Industrial
  4  Road            5  Coal Plant        6  Police
  7  Fire Station    8  Bulldoze          9  Power Line
  r  Railroad        k  Park              z  Stadium
  w  Seaport         a  Airport           n  Nuclear Plant

[bold #f0c080]GAMEPLAY[/]
  enter / space       apply current tool
  p                   pause / resume
  q                   quit

[bold #f0c080]DIALOGS[/]
  b                   budget editor
  g                   graphs (population, funds, RCI)
  o                   cycle overlay (pollution → crime → power → traffic → land → off)
  e                   evaluation
  t                   tutorial
  s                   save city
  l                   map legend
  L                   load city
  A                   AI advisor (Claude — needs ANTHROPIC_API_KEY)
  ?                   this screen

[dim]press escape to close[/]
"""


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Container {
        width: 72; height: 28;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("escape,q,question_mark", "app.pop_screen", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(Text.from_markup(HELP_TEXT))


# ---------- AI advisor --------------------------------------------------------


class AdvisorScreen(ModalScreen):
    """Full-screen narrative assessment from the Claude API. Opens with
    a spinner-ish placeholder, then fills in the response when the worker
    resolves. Dismiss with escape."""

    DEFAULT_CSS = """
    AdvisorScreen { align: center middle; }
    AdvisorScreen > Container {
        width: 84; height: 26;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    #advisor-body { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape,q,a", "app.pop_screen", "close"),
    ]

    def __init__(self, state_snapshot: dict) -> None:
        super().__init__()
        self.state = state_snapshot
        self._response: str | None = None

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("", id="advisor-body")

    def on_mount(self) -> None:
        self._render(loading=True)
        # Fire the Claude call on a worker thread so the UI stays live.
        self.run_worker(self._do_consult, thread=True, exclusive=True)

    def _do_consult(self) -> None:
        from . import advisor
        text = advisor.consult(self.state)
        self._response = text
        # Schedule a render on the main thread.
        self.app.call_from_thread(self._render, loading=False)

    def _render(self, loading: bool) -> None:
        t = Text()
        t.append("CITY ADVISOR\n", style="bold #f0c080")
        t.append(f"{'─' * 72}\n\n", style="dim")
        if loading:
            t.append("  Consulting advisor…\n", style="dim italic")
            t.append("  (first call takes 2-4s; subsequent ones are faster\n"
                     "   because the instructions are prompt-cached)\n",
                     style="dim")
        else:
            t.append_text(Text.from_markup(self._response or ""))
        t.append("\n\n[dim]escape to close[/]", style="dim")
        self.query_one("#advisor-body", Static).update(t)


# ---------- map legend --------------------------------------------------------


# Ordered reference for the map legend. Each entry picks a representative
# tile ID so the glyph + bg colour matches exactly what the map renders.
# Sourced from tiles._TABLE to stay in sync with any future glyph changes.
_LEGEND_ENTRIES: list[tuple[str, int]] = [
    # (section header OR label, representative tile id [or -1 for a header])
    ("TERRAIN", -1),
    ("open land (buildable)",    0),
    ("shallow water",            2),
    ("deep water",              10),
    ("trees",                   25),
    ("dense forest",            40),
    ("rubble",                  44),
    ("fire",                    60),
    ("flood",                   48),

    ("INFRASTRUCTURE", -1),
    ("road — horizontal",       66),
    ("road — vertical",         67),
    ("road — corner",           68),
    ("road — intersection",     76),
    ("road — busy traffic",    148),
    ("power line",             208),
    ("railroad",               224),

    ("ZONES (density shown by shading ░▒▓)", -1),
    ("residential (low)",      240),
    ("residential (hi)",       400),
    ("commercial (low)",       423),
    ("commercial (hi)",        580),
    ("industrial (low)",       612),
    ("industrial (hi)",        685),

    ("BUILDINGS", -1),
    ("coal power plant",       750),
    ("nuclear plant",          830),
    ("police station",         775),
    ("fire station",           770),
    ("stadium",                790),
    ("seaport",                920),
    ("airport",                720),
]


class LegendScreen(ModalScreen):
    """Quick reference for map symbols. Dismiss with any key."""

    DEFAULT_CSS = """
    LegendScreen { align: center middle; }
    LegendScreen > Container {
        width: 54; height: 36;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("escape,l,q,space,enter", "app.pop_screen", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("", id="legend-body")

    def on_mount(self) -> None:
        from . import tiles
        t = Text()
        t.append("MAP LEGEND\n", style="bold #f0c080")
        t.append(f"{'─' * 48}\n", style="dim")
        for label, tid in _LEGEND_ENTRIES:
            if tid == -1:
                t.append(f"\n{label}\n", style="bold #c89560")
                continue
            glyph, klass = tiles._TABLE[tid]
            style = tiles.style_for(klass)
            t.append("  ")
            t.append(f" {glyph} ", style=style)
            t.append(f"  {label}\n")
        t.append(
            "\n[dim]press any key (l, escape, space, q) to close[/]",
            style="dim",
        )
        self.query_one("#legend-body", Static).update(t)


# ---------- save / load -------------------------------------------------------


def _default_save_name(sim) -> str:
    """Auto-generate a filename from city name + in-game date."""
    name = (sim.cityName or "mycity").strip().replace(" ", "_")
    return f"{name}-{sim.cityYear}{sim.cityMonth + 1:02d}"


class ConfirmScreen(ModalScreen):
    """Generic yes/no confirmation modal. Dismissed with the boolean."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Container {
        width: 56; height: 9;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("y", "confirm", "yes"),
        Binding("n,escape,q", "cancel", "no"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(Text.from_markup(
                f"[bold #f0c080]CONFIRM[/]\n\n{self.prompt}\n\n"
                "[dim]y to confirm  ·  n / escape to cancel[/]"
            ))

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class SaveScreen(ModalScreen):
    """Prompt for a filename and save via sim.saveCityAs(). If the target
    file already exists, chain a ConfirmScreen before writing."""

    DEFAULT_CSS = """
    SaveScreen { align: center middle; }
    SaveScreen > Container {
        width: 60; height: 12;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    SaveScreen Input { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "app.pop_screen", "cancel")]

    def __init__(self, sim) -> None:
        super().__init__()
        self.sim = sim

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(Text.from_markup(
                "[bold #f0c080]SAVE CITY[/]\n\n"
                "Enter a filename (without the .cty suffix).\n"
                "[dim]enter to save  ·  escape to cancel[/]"
            ))
            yield Input(value=_default_save_name(self.sim), id="save-name")

    def on_mount(self) -> None:
        # Focus the input so the user can just start typing.
        self.query_one("#save-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip().replace("/", "_").replace("..", "")
        if not name:
            return
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        path = SAVE_DIR / f"{name}.cty"

        def _write_and_dismiss() -> None:
            self.sim.saveCityAs(str(path))
            ok = path.exists() and path.stat().st_size > 1000
            self.dismiss((ok, str(path)))

        if path.exists():
            # Chain confirm — user shouldn't silently lose an existing save.
            def _after_confirm(ok_to_overwrite: bool | None) -> None:
                if ok_to_overwrite:
                    _write_and_dismiss()
                else:
                    self.dismiss(None)  # canceled
            self.app.push_screen(
                ConfirmScreen(f"Overwrite existing save [bold]{path.name}[/]?"),
                _after_confirm,
            )
        else:
            _write_and_dismiss()


class LoadScreen(ModalScreen):
    """List user saves + built-in vendor scenarios, arrow-key nav, enter to
    load."""

    DEFAULT_CSS = """
    LoadScreen { align: center middle; }
    LoadScreen > Container {
        width: 68; height: 24;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    #load-body { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape,q,l", "app.pop_screen", "cancel"),
        Binding("j,tab",     "next", "down"),
        Binding("k,shift+tab", "prev", "up"),
        Binding("enter,space", "load_selected", "load"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[tuple[str, str, Path]] = []  # (group, label, path)
        self.sel: int = 0

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(id="load-body")

    def on_mount(self) -> None:
        self._build_entries()
        self._render()

    def _build_entries(self) -> None:
        self.entries.clear()
        if SAVE_DIR.exists():
            for p in sorted(SAVE_DIR.glob("*.cty")):
                self.entries.append(("saves", p.stem, p))
        if CITIES_DIR.exists():
            for p in sorted(CITIES_DIR.glob("*.cty")):
                self.entries.append(("scenarios", p.stem, p))

    def _render(self) -> None:
        t = Text()
        t.append("LOAD CITY\n", style="bold #f0c080")
        t.append(f"{'─' * 60}\n", style="dim")
        if not self.entries:
            t.append("\n  no saves or scenarios found\n", style="dim")
        else:
            last_group = None
            line_no = 0
            for group, label, _ in self.entries:
                if group != last_group:
                    t.append(f"\n  {group.upper()}\n", style="bold dim")
                    last_group = group
                marker = "▶ " if line_no == self.sel else "  "
                style = "bold reverse" if line_no == self.sel else ""
                t.append(f"  {marker}{label}\n", style=style)
                line_no += 1
        t.append("\n[dim]j/k navigate  ·  enter load  ·  escape cancel[/]",
                 style="dim")
        self.query_one("#load-body", Static).update(t)

    def action_next(self) -> None:
        if self.entries:
            self.sel = (self.sel + 1) % len(self.entries)
            self._render()

    def action_prev(self) -> None:
        if self.entries:
            self.sel = (self.sel - 1) % len(self.entries)
            self._render()

    def action_load_selected(self) -> None:
        if not self.entries:
            return
        _, _, path = self.entries[self.sel]
        self.dismiss(str(path))


# ---------- tutorial ----------------------------------------------------------


# Tutorial content is distilled from the official Micropolis manual
# (vendor/micropolis/micropolis-activity/manual/{intro,reference}.html,
# GPLv3 by Don Hopkins / Maxis), adapted to the TUI keyboard/mouse model.
TUTORIAL_PAGES: list[tuple[str, str]] = [
    ("Welcome, Mayor",
     "Enter Micropolis and take control. You are the [bold]Mayor and\n"
     "City Planner[/] with complete authority over a real-time city\n"
     "simulation.\n\n"
     "Your city is populated by [bold]Sims[/] — simulated citizens. Like\n"
     "their human counterparts, they build houses, stores, and factories.\n"
     "They also complain about taxes, mayors, and taxes. If they get\n"
     "unhappy they move out, and your city deteriorates.\n\n"
     "Time advances automatically (a month every few seconds). Pause\n"
     "whenever you like with [bold]p[/].\n\n"
     "[dim]n = next page   b = back   escape = close.[/]"),

    ("How the simulation works",
     "Micropolis is a [bold]System Simulation[/]. Your challenge is to\n"
     "figure out the rules and take control. The rules include:\n\n"
     "  • [bold]Human[/]      — residential space, jobs, quality of life\n"
     "  • [bold]Economic[/]   — land value, markets, power, taxation\n"
     "  • [bold]Survival[/]   — disasters, crime, pollution\n"
     "  • [bold]Political[/]  — public opinion, zoning, satisfaction\n\n"
     "You never \"win\" a System Simulation in the arcade sense — you\n"
     "use the [bold]Tools[/] to plan, zone, build, bulldoze, and manage\n"
     "the city you want."),

    ("The map",
     "The playfield is a [bold]120 × 100 tile grid[/]. The bright yellow\n"
     "cell is your [bold]cursor[/] — wherever you apply a tool, that's\n"
     "where it happens.\n\n"
     "Three terrain types:\n"
     "  [rgb(120,96,64) on rgb(40,30,18)] [/] [bold]Open land[/]  — where you can zone and build\n"
     "  [rgb(70,150,60) on rgb(18,40,20)]♣[/] [bold]Trees[/]      — bulldoze to clear, but lowers value\n"
     "  [rgb(60,110,180) on rgb(15,35,70)]≈[/] [bold]Water[/]     — bulldoze coast to make landfill\n\n"
     "Controls:\n"
     "  • [bold]arrows[/]       move the cursor\n"
     "  • [bold]click[/]        jump cursor + apply current tool\n"
     "  • [bold]drag[/]         draw with the tool (great for roads)"),

    ("Zones — where Sims live and work",
     "Sims won't build on raw land. You [bold]zone[/] 3×3 plots, and Sims\n"
     "develop them on their own — if they have power and road access.\n\n"
     "  [bold green]1[/]  Residential  $100   — where Sims live\n"
     "  [bold cyan]2[/]   Commercial   $100   — where Sims shop and work\n"
     "  [bold yellow]3[/]   Industrial   $100   — where Sims manufacture jobs\n\n"
     "The [bold]Demand Indicator[/] (CITY STATUS panel) shows relative\n"
     "demand for each type:\n"
     "  [bold green]green[/]  residential   [bold cyan]cyan[/]  commercial   [bold yellow]yellow[/] industrial\n\n"
     "Zone density rises visibly: [bold]░[/] light  [bold]▒[/] medium  [bold]▓[/] heavy.\n"
     "A balanced city has all three growing together."),

    ("Roads, rails, and power",
     "Every zone needs [bold]road access[/] AND [bold]power[/] to grow.\n\n"
     "  [bold]4[/]  Road         $10     every zone wants a road neighbour\n"
     "  [bold]r[/]  Rail         $20     faster, replaces roads for transit\n"
     "  [bold]9[/]  Power line   $5      wire carries electricity over gaps\n"
     "  [bold]5[/]  Coal plant   $3000   generates power, pollutes nearby\n"
     "  [bold]n[/]  Nuclear      $5000   cleaner, more power, meltdown risk\n\n"
     "Power flows through plants, wires, AND zones. You don't need to\n"
     "wire every tile — adjacent powered tiles pass it along.\n\n"
     "[dim]Drag the road tool to lay a straight road fast.\n"
     "If a zone refuses to grow, it probably lacks power or a road.[/]"),

    ("Services and amenities",
     "As the city grows, crime climbs and fires break out. Budget for:\n\n"
     "  [bold]6[/]  Police station  $500    lowers crime nearby\n"
     "  [bold]7[/]  Fire station    $500    fights fires nearby\n"
     "  [bold]k[/]  Park            $10     raises land value\n"
     "  [bold]z[/]  Stadium         $5000   needed for Metropolis class\n"
     "  [bold]w[/]  Seaport         $3000   boosts industry\n"
     "  [bold]a[/]  Airport         $10000  boosts commerce\n\n"
     "Press [bold]o[/] to cycle overlays:\n"
     "  [magenta]pollution[/]  [red]crime[/]  [yellow]power[/]  [#ffb03c]traffic[/]  [green]land value[/]  [cyan]pop density[/]\n\n"
     "Overlays give a physical and demographic overview — follow the\n"
     "trouble spots and intervene with services or bulldozing."),

    ("Money, budget, evaluation",
     "You start with fixed funds; tax revenue comes in each year.\n\n"
     "  [bold]b[/]  Budget       tax 0–20% and road/police/fire funding\n"
     "  [bold]g[/]  Graphs       pop, funds, R/C/I over time\n"
     "  [bold]e[/]  Evaluation   city class, score, averages, public opinion\n"
     "  [bold]s[/]  Save         current city to ~/.local/share/simcity-tui/\n"
     "  [bold]l[/]  Load         any save or built-in scenario\n"
     "  [bold]?[/]  Help         full keybindings at any time\n\n"
     "Cutting service funding saves money now but raises crime, lets\n"
     "roads decay, and lets fires spread. Raising tax slows growth.\n\n"
     "City classes rise with population: [dim]Village → Town → City →\n"
     "Capital → Metropolis → Megalopolis[/]. Reach Megalopolis and\n"
     "you've officially mastered the system."),

    ("Your first city — try this sequence",
     "1. Pause with [bold]p[/] so nothing ticks while you plan.\n"
     "2. Press [bold]5[/] and click empty land → build a [bold red]coal plant[/].\n"
     "3. Press [bold]4[/] and drag a [bold white]road[/] from the plant out\n"
     "   10–15 tiles.\n"
     "4. Press [bold]1[/] and place 3–4 [bold green]residential zones[/]\n"
     "   adjacent to the road.\n"
     "5. Press [bold]3[/] and place 2 [bold yellow]industrial zones[/] on\n"
     "   the far side (pollution stays there).\n"
     "6. Press [bold]2[/] and place a couple of [bold cyan]commercial zones[/]\n"
     "   in the middle where R and I meet.\n"
     "7. Unpause with [bold]p[/]. Over a game-year, zones should develop.\n\n"
     "[dim]Zones staying empty? Check road neighbour AND power. Press\n"
     "[bold]o[/] until the power-grid overlay shows yellow coverage.[/]"),

    ("Good luck, Mayor",
     "That's the core loop. The rest is iteration — watch the graphs,\n"
     "react to overlays, raise tax when cash flow is strong, lower it\n"
     "during a slump.\n\n"
     "Press [bold]?[/] anytime for the full keybinding sheet.\n"
     "Press [bold]t[/] to reopen this tutorial.\n\n"
     "[dim]escape to close and start building.[/]"),
]


class TutorialScreen(ModalScreen):
    DEFAULT_CSS = """
    TutorialScreen { align: center middle; }
    TutorialScreen > Container {
        width: 78; height: 28;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    #tut-body { height: 1fr; }
    #tut-nav { height: 1; color: #c89560; }
    """
    BINDINGS = [
        Binding("escape,q,t", "app.pop_screen", "close"),
        # Use n/b (next/back) rather than arrows — priority App bindings
        # would swallow arrow keys before reaching the modal.
        Binding("n,right,space,enter", "next", "next"),
        Binding("b,p,left", "prev", "prev"),
    ]

    def __init__(self, start_page: int = 0) -> None:
        super().__init__()
        self.page = start_page

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(id="tut-body")
            yield Static(id="tut-nav")

    def on_mount(self) -> None:
        self.render_page()

    def render_page(self) -> None:
        title, body = TUTORIAL_PAGES[self.page]
        n = len(TUTORIAL_PAGES)
        t = Text()
        t.append(f"TUTORIAL — {title}\n", style="bold #f0c080")
        t.append(f"{'─' * 60}\n", style="dim")
        t.append_text(Text.from_markup(body))
        self.query_one("#tut-body", Static).update(t)
        nav = Text()
        nav.append(f"  page {self.page + 1}/{n}   ", style="bold")
        nav.append("[n] next   [b] back   [escape] close",
                   style="dim")
        self.query_one("#tut-nav", Static).update(nav)

    def action_next(self) -> None:
        if self.page < len(TUTORIAL_PAGES) - 1:
            self.page += 1
            self.render_page()

    def action_prev(self) -> None:
        if self.page > 0:
            self.page -= 1
            self.render_page()


# ---------- budget ------------------------------------------------------------


class BudgetScreen(ModalScreen):
    DEFAULT_CSS = """
    BudgetScreen { align: center middle; }
    BudgetScreen > Container {
        width: 64; height: 22;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    #budget-body { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape,q,b", "app.pop_screen", "close"),
        Binding("plus,equals_sign,equal", "adjust_tax(+1)", "tax +", show=False),
        Binding("minus,underscore",  "adjust_tax(-1)", "tax −", show=False),
        Binding("r",     "cycle_fund('road')"),
        Binding("f",     "cycle_fund('fire')"),
        Binding("c",     "cycle_fund('police')"),
    ]

    def __init__(self, sim) -> None:
        super().__init__()
        self.sim = sim

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(id="budget-body")

    def on_mount(self) -> None:
        self.refresh_body()

    def refresh_body(self) -> None:
        s = self.sim
        t = Text()
        t.append("BUDGET\n", style="bold #f0c080")
        t.append(f"City: {s.cityName or 'MegaCity'}\n\n", style="dim")
        t.append(f"  Balance           ${s.totalFunds:>12,}\n", style="bold green")
        t.append(f"  Cash flow         ${s.cashFlow:>+12,}\n\n")
        t.append("TAX RATE  ", style="bold")
        t.append(f"{s.cityTax:>3d}%   ", style="bold yellow")
        t.append(_bar(s.cityTax, 20, 20), style="yellow")
        t.append("   [+/− adjust]\n\n", style="dim")
        t.append("FUNDING LEVELS   [r/c/f cycle 100/75/50/25/0%]\n", style="bold")
        t.append(f"  Road            {int(s.roadPercent*100):>3d}%   "
                 f"{_bar(int(s.roadPercent*100), 100, 20)}\n")
        t.append(f"  Police          {int(s.policePercent*100):>3d}%   "
                 f"{_bar(int(s.policePercent*100), 100, 20)}\n")
        t.append(f"  Fire            {int(s.firePercent*100):>3d}%   "
                 f"{_bar(int(s.firePercent*100), 100, 20)}\n\n")
        t.append("[dim]escape to close[/]", style="dim")
        self.query_one("#budget-body", Static).update(t)

    def action_adjust_tax(self, delta: str) -> None:
        new = max(0, min(20, self.sim.cityTax + int(delta)))
        self.sim.cityTax = new
        self.refresh_body()

    def action_cycle_fund(self, which: str) -> None:
        attr = {"road": "roadPercent", "police": "policePercent",
                "fire": "firePercent"}[which]
        levels = [1.0, 0.75, 0.5, 0.25, 0.0]
        cur = getattr(self.sim, attr)
        # Snap to nearest, then advance.
        idx = min(range(len(levels)), key=lambda i: abs(levels[i] - cur))
        setattr(self.sim, attr, levels[(idx + 1) % len(levels)])
        self.refresh_body()


# ---------- graphs ------------------------------------------------------------


_GRAPH_METRICS: list[tuple[str, str, str, str]] = [
    # (key, display label, history-row field, rich style)
    ("1", "Population",      "cityPop",    "bold rgb(80,220,80)"),
    ("2", "Funds ($)",       "totalFunds", "bold rgb(255,220,80)"),
    ("3", "Residential Pop", "resPop",     "bold rgb(100,220,100)"),
    ("4", "Commercial Pop",  "comPop",     "bold rgb(100,180,240)"),
    ("5", "Industrial Pop",  "indPop",     "bold rgb(230,220,90)"),
    ("6", "City Score",      "cityScore",  "bold rgb(230,100,230)"),
]


class GraphsScreen(ModalScreen):
    """Full-screen graph view — one metric at a time, rendered as a tall
    column bar chart that fills the terminal. Press 1..6 to switch
    metrics, left/right (or h/l) to not yet implemented horizontal scroll.
    """
    DEFAULT_CSS = """
    GraphsScreen { align: center middle; }
    GraphsScreen > Container {
        width: 100%; height: 100%;
        background: #0a0a0c;
        padding: 1 2;
    }
    #graph-title { height: 2; color: #f0c080; }
    #graph-body  { height: 1fr; }
    #graph-footer { height: 2; color: #c89560; }
    """
    BINDINGS = [
        Binding("escape,q,g", "app.pop_screen", "close"),
        *[Binding(k, f"pick_metric('{k}')", show=False)
          for k, *_ in _GRAPH_METRICS],
        Binding("tab,n",       "next_metric", "next"),
        Binding("shift+tab,p", "prev_metric", "prev"),
    ]

    def __init__(self, history: list[dict]) -> None:
        super().__init__()
        self.history = history
        self.metric_idx = 0  # default: Population

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(id="graph-title")
            yield Static(id="graph-body")
            yield Static(id="graph-footer")

    def on_mount(self) -> None:
        self.render_graph()

    def render_graph(self) -> None:
        key, label, field, style = _GRAPH_METRICS[self.metric_idx]
        h = self.history
        # Title bar.
        title = Text()
        title.append("GRAPHS — ", style="bold #f0c080")
        title.append(label, style=style)
        if h:
            first, last = h[0], h[-1]
            title.append(
                f"   {len(h)} samples   "
                f"{first['year']}-{first['month']+1:02d}"
                f"  →  {last['year']}-{last['month']+1:02d}   "
                f"latest: {last[field]:,}",
                style="dim",
            )
        self.query_one("#graph-title", Static).update(title)
        # Body: big chart that fills the terminal.
        term_w, term_h = self.app.size.width, self.app.size.height
        chart_w = max(20, term_w - 8)
        chart_h = max(4, term_h - 10)
        body = Text()
        if not h:
            body.append(
                "\n  no history yet — leave the game running for a few in-game\n"
                "  months and the graph will populate one sample per month.\n",
                style="dim",
            )
        else:
            values = [row[field] for row in h]
            lo, hi = min(values), max(values)
            # Y-axis labels — 3 rows (top, middle, bottom).
            body.append_text(_tall_chart(values, chart_w, chart_h, style=style))
            body.append(f"  min: {lo:,}   max: {hi:,}\n", style="dim")
        self.query_one("#graph-body", Static).update(body)
        # Footer — metric picker.
        foot = Text()
        for i, (k, mlabel, _, mstyle) in enumerate(_GRAPH_METRICS):
            if i == self.metric_idx:
                foot.append(f" [{k}] {mlabel} ", style=mstyle + " reverse")
            else:
                foot.append(f" [{k}] {mlabel} ", style="dim")
        foot.append("   tab/shift+tab cycle  ·  escape close", style="dim")
        self.query_one("#graph-footer", Static).update(foot)

    def action_pick_metric(self, key: str) -> None:
        for i, (k, *_) in enumerate(_GRAPH_METRICS):
            if k == key:
                self.metric_idx = i
                break
        self.render_graph()

    def action_next_metric(self) -> None:
        self.metric_idx = (self.metric_idx + 1) % len(_GRAPH_METRICS)
        self.render_graph()

    def action_prev_metric(self) -> None:
        self.metric_idx = (self.metric_idx - 1) % len(_GRAPH_METRICS)
        self.render_graph()


# ---------- evaluation --------------------------------------------------------


_CITY_CLASS = {
    0: "Village", 1: "Town", 2: "City",
    3: "Capital", 4: "Metropolis", 5: "Megalopolis",
}


class EvaluationScreen(ModalScreen):
    DEFAULT_CSS = """
    EvaluationScreen { align: center middle; }
    EvaluationScreen > Container {
        width: 64; height: 22;
        border: round #c89560; background: #0e0e10;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("escape,q,e", "app.pop_screen", "close"),
    ]

    def __init__(self, sim) -> None:
        super().__init__()
        self.sim = sim

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(id="eval-body")

    def on_mount(self) -> None:
        s = self.sim
        t = Text()
        t.append("CITY EVALUATION\n", style="bold #f0c080")
        t.append(f"City: {s.cityName or 'MegaCity'}\n\n", style="dim")
        city_class = _CITY_CLASS.get(s.cityClass, f"Class {s.cityClass}")
        t.append(f"Class            {city_class}\n", style="bold")
        t.append(f"Score            {s.cityScore} / 1000   "
                 f"{_bar(s.cityScore, 1000, 20)}\n")
        t.append(f"Population       {max(s.cityPop, 0):>8,}\n")
        t.append(f"R / C / I        {s.resPop:>5d} / {s.comPop:>3d} / {s.indPop:>3d}\n\n")
        t.append("Averages (0–255)\n", style="bold")
        t.append(f"  Pollution       {s.pollutionAverage:>4d}   "
                 f"{_bar(s.pollutionAverage, 255, 20)}\n")
        t.append(f"  Crime           {s.crimeAverage:>4d}   "
                 f"{_bar(s.crimeAverage, 255, 20)}\n")
        t.append(f"  Traffic         {s.trafficAverage:>4d}   "
                 f"{_bar(s.trafficAverage, 255, 20)}\n")
        t.append(f"  Land value      "
                 f"{getattr(s, 'landValueAverage', 0):>4d}   "
                 f"{_bar(getattr(s, 'landValueAverage', 0), 255, 20)}\n\n")
        t.append(f"Tax rate         {s.cityTax}%\n")
        t.append(f"Cash flow        ${s.cashFlow:+,}\n\n")
        t.append("[dim]escape to close[/]", style="dim")
        self.query_one("#eval-body", Static).update(t)


# ---------- overlay helpers ---------------------------------------------------

# The engine keeps several downsampled "density" maps alongside the tile grid.
# Each overlay is a 15×12 byte array (WORLD_W//8 × WORLD_H//8), one cell per
# 8×8 tile block. MapView consults whichever is active via overlay_sample().


OVERLAY_MODES = [
    "off",
    "pollution",
    "crime",
    "power",
    "traffic",
    "land_value",
    "pop_density",
]

_OVERLAY_GLYPHS = " ░▒▓█"

_OVERLAY_COLORS = {
    "pollution":   "bold rgb(200,80,200)",
    "crime":       "bold rgb(220,80,80)",
    "power":       "bold rgb(255,220,80)",
    "traffic":     "bold rgb(255,180,60)",
    "land_value":  "bold rgb(140,220,120)",
    "pop_density": "bold rgb(120,200,255)",
}


def overlay_buffer(sim, mode: str) -> bytes | None:
    """Fetch a snapshot of the overlay buffer for `mode`. Returns a 180-byte
    bytes object (15×12 u8 downsampled map), or None if mode == 'off'."""
    if mode == "off":
        return None
    fn_map = {
        "pollution":   sim.getPollutionDensityMapBuffer,
        "crime":       sim.getCrimeRateMapBuffer,
        "power":       sim.getPowerGridMapBuffer,
        "traffic":     sim.getTrafficDensityMapBuffer,
        "land_value":  sim.getLandValueMapBuffer,
        "pop_density": sim.getPopulationDensityMapBuffer,
    }
    fn = fn_map.get(mode)
    if fn is None:
        return None
    ptr = int(fn())
    # 15 × 12 = 180 bytes, one byte per 8×8 tile block.
    return ctypes.string_at(ptr, 180)


def overlay_glyph_and_color(mode: str, cell_byte: int) -> tuple[str, str]:
    """Map a 0-255 density byte to a block char and a style string."""
    # 5 bins: 0 = off (space), then ░ ▒ ▓ █ for ascending density.
    idx = min(4, cell_byte // 52)  # 0..4
    glyph = _OVERLAY_GLYPHS[idx]
    color = _OVERLAY_COLORS.get(mode, "white")
    return glyph, color
