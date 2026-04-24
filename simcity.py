"""Entry point — `python simcity.py [city-name] [--agent] [--headless]`."""

from __future__ import annotations

import argparse

from micropolis_tui.app import run


def main() -> None:
    p = argparse.ArgumentParser(prog="micropolis-tui")
    p.add_argument("city", nargs="?", default="haight",
                   help="city save name under vendor/.../cities/ (default: haight)")
    p.add_argument("--agent", action="store_true",
                   help="start the agent HTTP API alongside the TUI")
    p.add_argument("--agent-port", type=int, default=8787,
                   help="port for the agent API (default: 8787)")
    p.add_argument("--headless", action="store_true",
                   help="no TUI, run sim + agent API only (implies --agent)")
    p.add_argument("--no-sound", action="store_true",
                   help="disable sound effects (on by default)")
    p.add_argument("--no-music", action="store_true",
                   help="disable the background chiptune loop (on by default)")
    args = p.parse_args()

    agent_port = args.agent_port if (args.agent or args.headless) else None
    run(args.city, agent_port=agent_port, headless=args.headless,
        sound=not args.no_sound, music=not args.no_music)


if __name__ == "__main__":
    main()
