"""Opt-in sound effects.

Prefers real Micropolis .wav files from the vendored SimHacker/micropolis tree
(GPLv3 by Don Hopkins / Maxis) — 160+ sound samples covering every gameplay
event. When a sample isn't available or the WAV doesn't exist, falls back to
a stdlib-synthesised tone so the game is never silently missing feedback.

Plays via whichever of `paplay` / `aplay` / `afplay` is on PATH. Design
contract: "silent-on-failure". No audio pipeline, no player, no speakers,
SSH-only shell — nothing happens. Never crash, never block, never spam logs.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path

# Vendored Micropolis sound directory — checked at init. All paths are
# resolved relative to the repo root, discovered from this file's location.
_VENDOR_SFX_DIR = (
    Path(__file__).resolve().parent.parent
    / "vendor" / "micropolis" / "micropolis-activity" / "res" / "sounds"
)

# Map our internal sound names to the best-matching vendor WAV file.
#
# Many of Micropolis's original clips are *human voice* recordings, not
# SFX ("sorry", "bulldozer", "ignition" etc.). They're authentic but
# jarring — `ignition.wav` is 3s of an engine starting, which was going
# off every year rollover. We route those three events to our synth
# fallback and keep only the short, punchy vendor SFX here.
#
# If a name doesn't appear below, `_ensure` falls through to the synth
# tone defined in _SOUND_SPECS.
_VENDOR_SOUNDS: dict[str, str] = {
    "click":    "beep.wav",          # 0.11 s — pure beep, not the voice clip
    "build":    "oop.wav",            # 0.11 s — place-a-thing bloop
    "bulldoze": "rumble.wav",         # 0.27 s — rumbling-bulldozer engine
    "deny":     "boing.wav",          # 0.43 s — classic "nope" bounce
    "disaster": "explosion-hi.wav",   # authentic 8-bit explosion
    # Intentionally NOT mapped → synth fallback kicks in:
    #   chime — "ignition" engine startup; synth bell is cleaner
    #
    # Other vendor samples you could wire up with one line:
    # honkhonk-hi/low/med, siren, skid, woosh, quackquack, whip,
    # monster, cuckoo, heavytraffic, coal, nuclear, police,
    # airport, seaport, stadium, query, aaah, uhuh, ...
}


# (name, frequency Hz list — summed), (duration_s), (attack_ms, decay_ms)
_SOUND_SPECS: dict[str, tuple[list[int], float, int, int]] = {
    # High cheerful blip — construction ok.
    "build":     ([880, 1320],    0.09, 5, 30),
    # Sharper click — tool selection.
    "click":     ([1500],         0.04, 2, 15),
    # Low thunk — bulldozer demolishing.
    "bulldoze":  ([220, 110],     0.13, 5, 40),
    # Descending buzz — can't afford / wrong place.
    "deny":      ([300, 200],     0.18, 5, 60),
    # Chime — happy event (new year, class-up).
    "chime":     ([660, 880, 1100], 0.35, 20, 200),
    # Rumble — disaster.
    "disaster":  ([80, 100, 120], 0.45, 10, 300),
}


def _synth(freqs: list[int], duration: float, attack_ms: int,
           decay_ms: int, sample_rate: int = 22_050) -> bytes:
    """Generate a PCM16 mono wave with linear attack/decay envelope."""
    n = int(sample_rate * duration)
    attack = int(sample_rate * attack_ms / 1000)
    decay = int(sample_rate * decay_ms / 1000)
    attack = min(attack, n // 2)
    decay = min(decay, n - attack)
    frames = bytearray()
    for i in range(n):
        if i < attack:
            env = i / max(attack, 1)
        elif i > n - decay:
            env = max(0.0, (n - i) / max(decay, 1))
        else:
            env = 1.0
        t = i / sample_rate
        sample = 0.0
        for f in freqs:
            sample += math.sin(2 * math.pi * f * t)
        sample = (sample / len(freqs)) * env * 0.3  # headroom
        frames.extend(struct.pack("<h", int(sample * 32767)))
    return bytes(frames)


def _detect_player() -> list[str] | None:
    """Pick the first audio player available on PATH."""
    candidates = [
        ["paplay"],              # PulseAudio / PipeWire
        ["aplay", "-q"],         # ALSA
        ["afplay"],              # macOS
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


class SoundBoard:
    """Lazily-synthesised tone set, played in the background via subprocess."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._player = _detect_player() if enabled else None
        self._paths: dict[str, Path] = {}
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._failed = False
        # Debounce — a mouse drag across 20 tiles would otherwise spawn
        # 20 aplay processes simultaneously. Per-sound gate on "too soon
        # since last play" keeps things sane.
        self._last_played: dict[str, float] = {}
        self._min_gap_s: float = 0.15
        if self.enabled and self._player is None:
            # No player found — disable silently. The first log_msg in the
            # app will note this.
            self._failed = True
            self.enabled = False

    def _ensure(self, name: str) -> Path | None:
        if not self.enabled or self._failed:
            return None
        if name in self._paths:
            return self._paths[name]
        # Priority 1: real Micropolis sample from the vendor tree.
        vendor_file = _VENDOR_SOUNDS.get(name)
        if vendor_file is not None:
            vendor_path = _VENDOR_SFX_DIR / vendor_file
            if vendor_path.exists():
                self._paths[name] = vendor_path
                return vendor_path
        # Priority 2: synth fallback for anything without a vendor match.
        if name not in _SOUND_SPECS:
            return None
        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="simcity-sfx-")
        freqs, dur, atk, dcy = _SOUND_SPECS[name]
        data = _synth(freqs, dur, atk, dcy)
        path = Path(self._tmpdir.name) / f"{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22_050)
            w.writeframes(data)
        self._paths[name] = path
        return path

    def play(self, name: str) -> None:
        """Fire-and-forget. Any failure disables sound silently.

        Debounced per sound name: if the same sound was played less than
        `_min_gap_s` ago, silently drop this call. Protects against drag
        events that would otherwise stack 20+ subprocesses per second."""
        if not self.enabled:
            return
        now = time.monotonic()
        last = self._last_played.get(name, 0.0)
        if now - last < self._min_gap_s:
            return
        self._last_played[name] = now
        path = self._ensure(name)
        if path is None or self._player is None:
            return
        try:
            # Fully detached so we don't block or collect zombies. Redirect
            # output so a crash in the player doesn't spam the terminal.
            subprocess.Popen(
                [*self._player, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, FileNotFoundError):
            # Disable for the rest of the session rather than log every time.
            self.enabled = False
            self._failed = True

    def close(self) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
            self._tmpdir = None
