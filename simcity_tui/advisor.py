"""City advisor — calls the Claude API with a city-state snapshot and
returns a short narrative assessment plus 2-3 suggested actions.

Uses prompt caching on the system prompt so repeat invocations during one
play session are cheap (the instructions are identical; only the snapshot
changes). API key from env var ANTHROPIC_API_KEY; failure is graceful —
no key, no network, bad response → an error string surfaced to the UI.
"""

from __future__ import annotations

import os
from typing import Any


ADVISOR_MODEL = "claude-sonnet-4-6"


SYSTEM_PROMPT = """You are the City Advisor in a Micropolis-style city
simulation. The player is the mayor. You receive a JSON snapshot of the
current city and return a short (≤150 words) narrative assessment in
this structure:

HEADLINE: 6-10 words, the dominant theme right now.
ANALYSIS: 2-3 short sentences, plain English. Reference specific
  numbers from the snapshot so the player sees you're reading it.
DO NOW: 2-3 bullet actions. Each 8-15 words. Be concrete — name tools,
  tile classes, districts (e.g. "industrial cluster north of road 12").

Style rules:
  • Be direct. Skip flattery.
  • If pollution > 80 or crime > 80, escalate urgency.
  • If funds < cashFlow × 3, warn about bankruptcy risk.
  • If R/C/I are imbalanced (one type << others), suggest zoning.
  • No emojis. Plain markdown bold (**word**) is fine.
  • Never invent numbers. If the snapshot lacks a field, say so.

Micropolis tools referenced by key:
  1 Residential, 2 Commercial, 3 Industrial, 4 Road, 5 Coal plant,
  6 Police, 7 Fire, 8 Bulldoze, 9 Power line, r Rail, k Park,
  z Stadium, w Seaport, a Airport, n Nuclear plant.
"""


def available() -> bool:
    """True if the advisor can attempt a call (key + SDK present)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def consult(state: dict[str, Any]) -> str:
    """Synchronous call. Returns markdown-ish text or a "(advisor
    unavailable: …)" error string. The caller should run this off the
    UI thread; a 2-4 second delay is normal."""
    if not available():
        return ("(advisor unavailable: ANTHROPIC_API_KEY not set — "
                "export your key to enable narrative advice)")

    try:
        import anthropic
    except ImportError as e:
        return f"(advisor unavailable: anthropic SDK not installed — {e})"

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=ADVISOR_MODEL,
            max_tokens=400,
            # Cache the system prompt so repeated advisor calls in one
            # session only pay full cost once.
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": (
                    "Assess this city and advise. Snapshot follows.\n\n"
                    f"{state}"
                ),
            }],
        )
        # Extract the text content from the first block.
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return "(advisor returned no text)"
    except Exception as e:  # network error, auth error, etc.
        return f"(advisor error: {type(e).__name__}: {e})"
