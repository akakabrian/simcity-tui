"""Loads the vendored Micropolis SWIG binding from vendor/…/objs/.

Keeps the binding out of the package so `make engine` can rebuild it without
touching the Python source tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR_OBJS = REPO / "vendor" / "micropolis" / "MicropolisCore" / "src" / "MicropolisEngine" / "objs"
CITIES_DIR = REPO / "vendor" / "micropolis" / "MicropolisCore" / "src" / "cities"

if not (VENDOR_OBJS / "_micropolisengine.so").exists():
    raise RuntimeError(
        f"Micropolis engine not built. Run `make engine` (looked in {VENDOR_OBJS})."
    )

sys.path.insert(0, str(VENDOR_OBJS))
import micropolisengine as _me  # noqa: E402  # pyright: ignore[reportMissingImports]

Micropolis = _me.Micropolis
WORLD_W = _me.WORLD_W
WORLD_H = _me.WORLD_H


def new_sim(city: str | Path = "haight") -> _me.Micropolis:
    """Fresh Micropolis instance with a demo city loaded."""
    sim = Micropolis()
    sim.initGame()
    path = Path(city)
    if not path.is_absolute():
        path = CITIES_DIR / f"{city}.cty"
    if not sim.loadCity(str(path)):
        raise RuntimeError(f"loadCity failed: {path}")
    sim.setSpeed(2)
    sim.setPasses(1)
    return sim
