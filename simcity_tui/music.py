"""Background chiptune music — fire-and-forget subprocess loop.

Plays `simcity_tui/assets/music/<track>.mp3` in an infinite loop via
`paplay` / `afplay`. Same design contract as SoundBoard: silent on
failure (no audio pipeline, no player, SSH session, etc.), explicit
stop on app exit.

Format note: we bundle MP3 (not OGG) because **macOS `afplay` doesn't
decode OGG Vorbis** — it's Core Audio only (AAC/MP3/AIFF/WAV). An
OGG track would exit after a fraction of a second on Mac and the
`while true` loop would restart it, producing an audible "resetting
every few seconds" bug. MP3 at 96 kbps is decoded correctly by both
afplay and paplay and is ~865 KB for a 73-second chiptune loop.

`aplay` is intentionally NOT used — Linux `aplay` is ALSA raw and
doesn't decode MP3 either.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


MUSIC_DIR = Path(__file__).resolve().parent / "assets" / "music"
DEFAULT_TRACK = MUSIC_DIR / "lasso_lady.mp3"


def _detect_player() -> list[str] | None:
    """Pick a player that decodes MP3. paplay (PulseAudio/PipeWire) and
    afplay (macOS Core Audio) both do; aplay doesn't."""
    for cmd in (["paplay"], ["afplay"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


class MusicPlayer:
    """One-track looping background music. Starts with start(), stops on
    stop() or when the app process exits."""

    def __init__(self, enabled: bool = True,
                 track: Path = DEFAULT_TRACK) -> None:
        self.enabled = enabled
        self.track = track
        self._player = _detect_player() if enabled else None
        self._proc: subprocess.Popen | None = None
        if enabled and (self._player is None or not track.exists()):
            # Silently disable — same contract as SoundBoard.
            self.enabled = False

    def start(self) -> None:
        """Spawn a shell loop that plays the track forever. `paplay` /
        `afplay` block until playback finishes, so a `while true` loop
        gives us gapless repeat. start_new_session so terminal signals
        don't forward to the player process."""
        if not self.enabled or self._proc is not None:
            return
        try:
            player_cmd = " ".join(self._player or [])
            loop_cmd = f'while true; do {player_cmd} "{self.track}" >/dev/null 2>&1; done'
            self._proc = subprocess.Popen(
                ["bash", "-c", loop_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, FileNotFoundError):
            self.enabled = False

    def stop(self) -> None:
        """Kill the loop subprocess and its child player. Uses killpg
        because start_new_session() put them in their own group."""
        if self._proc is None:
            return
        import os
        import signal
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self._proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self._proc = None
