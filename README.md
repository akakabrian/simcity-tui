# simcity-tui

Terminal-native [SimCity / Micropolis](https://github.com/SimHacker/micropolis),
with a mouse-and-keyboard TUI (Textual), a REST API for AI agents, and
real-time Claude-powered advice.

- **Full 120×100 Micropolis simulation** via SWIG Python bindings around
  Don Hopkins' GPLv3 C++ engine
- **Mouse + keyboard** — click tools, click-drag for roads, left-click +
  right-click to zone rectangles
- **Map overlays** — pollution / crime / power / traffic / land value /
  population density
- **Graphs** — full-screen column bar charts (6 metrics, 60+ months)
- **REST agent API** on localhost — `GET /state`, `POST /tool`,
  `POST /advance`, `GET /events` (SSE); fully headless too (`--headless`)
- **Tutorial** cribbed from the official Micropolis manual
- **Sound** via real vendored Micropolis WAV samples, fail-silent when
  audio isn't available
- **LLM advisor** — press `A` to ask Claude for narrative city advice
  (needs `ANTHROPIC_API_KEY`)
- **Save / load / autosave** — every 5 game years, rotating 3-slot buffer
- **45+ headless QA scenarios** via Textual Pilot; dedicated benchmarks

## Requirements

- Python 3.10+
- `swig`, `g++`, `python3-dev`, `make`
- Linux, macOS. (Windows would work via WSL.)

On Ubuntu / Debian / Linux Mint:
```
sudo apt-get install swig g++ python3-dev python3-venv make
```

On macOS:
```
brew install swig python@3.12
# g++ is in the Xcode Command Line Tools
xcode-select --install
```

> **Why Homebrew Python?** The build uses `python3-config --includes`
> to locate the Python headers. Apple's bundled `/usr/bin/python3`
> doesn't ship `python3-config` on PATH, so the Makefile would
> silently get an empty include path and fail with a less obvious
> error. `python@3.12` from Homebrew comes with it.

## First-time setup

```
git clone https://github.com/<you>/simcity-tui.git
cd simcity-tui
make all
```

`make all` does three things in sequence:
1. `bootstrap` — clones SimHacker/micropolis into `vendor/` (~153 MB,
   one-time) and patches the SWIG glue for Python 3
2. `engine` — runs SWIG + `g++` to build `_micropolisengine.so`
3. `venv` — creates `.venv` and installs the Python deps

Takes 1–3 minutes the first time, seconds on subsequent rebuilds.

## Playing

```
.venv/bin/python simcity.py           # default city
.venv/bin/python simcity.py bluebird  # a specific vendor scenario
.venv/bin/python simcity.py --sound     # enable sound effects
.venv/bin/python simcity.py --no-music  # disable background chiptune (on by default)
.venv/bin/python simcity.py --agent     # also start the REST agent API on :8787
.venv/bin/python simcity.py --headless  # no TUI, just sim + agent API
```

See the in-game tutorial (press `t`) or the full keymap (press `?`).

## Keymap

```
MOVEMENT        1-9, r k z w a n   select tool
arrows          move cursor        click            apply + set rect anchor
mouse drag      continuous draw    right-click      fill rect from anchor
enter / space   apply tool

DIALOGS
b  budget       g  graphs          e  evaluation
t  tutorial     l  map legend      o  cycle overlay
s  save city    L  load city       A  AI advisor (Claude)
?  help         p  pause           q  quit
escape          cancel rect anchor
```

## Agent API

When launched with `--agent` (or `--headless`), a REST server runs on
`127.0.0.1:8787`. Key endpoints:

| | |
|--|--|
| `GET /state`               | live city snapshot (pop, funds, averages, cursor, overlay mode) |
| `GET /map?fmt=ids\|cls`   | full 120×100 tile grid |
| `GET /overlays/<name>`     | downsampled density map (pollution, crime, …) |
| `GET /history`             | monthly stats history |
| `GET /events`              | server-sent events stream of state snapshots |
| `POST /tool`               | `{code, x, y, [x2, y2]}` — apply tool / drag line |
| `POST /advance`            | `{ticks: N}` — tick sim N times while paused |
| `POST /pause`              | `{paused: bool}` |
| `POST /overlay`            | `{mode: "pollution" \| … \| "off"}` |
| `POST /tax`                | `{rate: 0..20}` |

See `simcity_tui/agent_api.py`. The schema is loosely modelled on
[hallucinating-splines](https://github.com/andrewedunn/hallucinating-splines).

## Tests

```
.venv/bin/python -m tests.qa         # 45+ TUI scenarios via Textual Pilot
.venv/bin/python -m tests.api_qa     # REST endpoint scenarios
.venv/bin/python -m tests.perf       # hot-path benchmarks
.venv/bin/python -m tests.play       # AI self-player builds a small city
.venv/bin/python -m tests.sound_test # diagnose audio pipeline
```

Or just `make test` to run the first three.

## Credits / license

- Engine: GPLv3 Micropolis (née SimCity) by Will Wright / Maxis,
  open-sourced & maintained by Don Hopkins.
- Sounds: GPLv3, vendored from
  `vendor/micropolis/micropolis-activity/res/sounds/`.
- Background music: [Lasso Lady (seamless loop)](https://opengameart.org/content/lasso-lady-seamless-loop),
  CC0 Public Domain. See `simcity_tui/assets/music/CREDITS.md`.
- This TUI: GPLv3 (derivative of a GPLv3 engine).
- API schema inspired by
  [andrewedunn/hallucinating-splines](https://github.com/andrewedunn/hallucinating-splines).

See `LICENSE`.
