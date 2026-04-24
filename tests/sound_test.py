"""Sound diagnostic — reports what audio player was detected, whether the
vendor WAV files exist, and attempts to play one sample so you can
confirm the pipeline end-to-end.

    .venv/bin/python -m tests.sound_test
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from micropolis_tui.sounds import (
    _VENDOR_SFX_DIR, _VENDOR_SOUNDS, SoundBoard, _detect_player,
)


def main() -> int:
    print("=" * 60)
    print(" micropolis-tui sound diagnostic")
    print("=" * 60)
    print(f" platform      : {platform.system()} {platform.release()}")
    print(f" python        : {sys.version.split()[0]}")
    print()

    # 1. Player detection
    print("[1] audio player detection")
    for cmd in (["paplay"], ["aplay", "-q"], ["afplay"]):
        path = shutil.which(cmd[0])
        mark = "✓" if path else "·"
        print(f"    {mark} {cmd[0]:<8} → {path or '(not found)'}")
    chosen = _detect_player()
    print(f"    → chosen: {chosen}")
    print()

    # 2. Vendor assets
    print("[2] vendor wav directory")
    print(f"    path: {_VENDOR_SFX_DIR}")
    print(f"    exists: {_VENDOR_SFX_DIR.exists()}")
    if _VENDOR_SFX_DIR.exists():
        wavs = sorted(_VENDOR_SFX_DIR.glob("*.wav"))
        print(f"    wav files: {len(wavs)}")
    print()

    # 3. Mapped sounds
    print("[3] game sound → vendor file mapping")
    for name, fn in _VENDOR_SOUNDS.items():
        p = _VENDOR_SFX_DIR / fn
        mark = "✓" if p.exists() else "✗"
        size = f"{p.stat().st_size:>6} bytes" if p.exists() else "MISSING"
        print(f"    {mark} {name:<10} → {fn:<20} {size}")
    print()

    # 4. End-to-end play via SoundBoard
    print("[4] SoundBoard end-to-end")
    board = SoundBoard(enabled=True)
    print(f"    enabled: {board.enabled}")
    print(f"    player:  {board._player}")
    if not board.enabled:
        print("    (board disabled — nothing to play)")
        print()
        return 1

    print()
    print("[5] direct subprocess test — tries to play one sample synchronously")
    print("    so you get a real error message if the pipeline fails")
    sample = _VENDOR_SFX_DIR / "zone.wav"
    if not sample.exists():
        print(f"    sample missing: {sample}")
        return 1
    print(f"    playing: {sample}")
    print(f"    using:   {board._player}")
    try:
        r = subprocess.run(
            [*board._player, str(sample)],
            capture_output=True,
            timeout=3,
        )
        print(f"    exit code: {r.returncode}")
        if r.stderr:
            print(f"    stderr: {r.stderr.decode(errors='replace').strip()}")
    except subprocess.TimeoutExpired:
        print("    (timed out — player ran >3s; probably fine)")
    except Exception as e:
        print(f"    error: {type(e).__name__}: {e}")
        return 1
    print()
    print("[6] debounced board.play() — 3 rapid calls; expect 1 audible")
    board.play("build")
    board.play("build")
    board.play("build")
    time.sleep(0.5)
    board.close()

    # Guidance
    print()
    print("=" * 60)
    print(" interpretation")
    print("=" * 60)
    print("""
  • If step [5] printed exit code 0 and you DIDN'T hear anything,
    the process is running where the audio lives. Common causes:
      - You SSH'd in from another machine. Sound plays on the
        remote box, not your local Mac/Windows. Run the game on
        the machine whose speakers you want to use.
      - The Linux box has no audio output configured (no speakers,
        no virtual sink, VNC session without PulseAudio).
      - Volume is muted.

  • If step [5] printed a non-zero exit code, the player binary
    exists but the pipeline is broken. Read the stderr message
    above — usually "No such device" or "connection refused".

  • If step [1] showed NO player found, install one:
      - macOS:  already has /usr/bin/afplay built-in (nothing to do).
      - Linux:  apt install pulseaudio-utils (for paplay) or alsa-utils.

  • To play the game on your Mac locally, clone the repo there,
    run `make engine` to build the .so, then `.venv/bin/python
    simcity.py --sound`. afplay will be picked up automatically.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
