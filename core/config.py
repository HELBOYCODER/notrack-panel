"""Central configuration for the NoTrack panel."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PANEL_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- upstream
NOTRACK_BASE = "https://notrack.ai"
DISPATCH_URL = f"{NOTRACK_BASE}/api/dispatch"
CHATS_URL = f"{NOTRACK_BASE}/api/chats"
CHAT_URL = f"{NOTRACK_BASE}/api/chat"

USER_AGENT = os.environ.get(
    "PANEL_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# ---------------------------------------------------------------- speakers
# 'C' = NoTrack's own model (the default chat), 'A' = Minimax, 'B' = ChatGPT.
# All three were observed in the page source (MODEL_LABEL_A/B/C). B was down
# at recon time; the panel probes liveness instead of assuming it.
SPEAKERS = {
    "C": {"id": "C", "name": "NoTrack", "vendor": "notrack", "default": True},
    "A": {"id": "A", "name": "Minimax", "vendor": "minimax", "default": False},
    "B": {"id": "B", "name": "ChatGPT", "vendor": "openai", "default": False},
}

PERSONAS = ["normal", "concise", "detailed", "creative"]

# ---------------------------------------------------------------- limits
# Measured against the live endpoint during recon.
INSTRUCTION_CHAR_CAP = 1980      # >2000 -> instr_refused{reason: too_long}
USER_INPUT_CAP = 4000            # free tier; plus=12000, pro=20000

# ---------------------------------------------------------------- pacing
# The upstream limiter is bursty and apparently layered (per-uid AND per-IP).
# Observed: 2 rapid requests on one uid pass, the 3rd 429s. We pace conservatively
# and back off adaptively rather than trying to squeeze the last request out.
PACE_MIN_INTERVAL = float(os.environ.get("PANEL_PACE", "1.9"))   # seconds between sends
PACE_MAX_INTERVAL = 12.0
MAX_RETRIES = 5
REQUEST_TIMEOUT = 180.0

# ---------------------------------------------------------------- panel auth
# Local API keys for the OpenAI-compatible shim. Stored hashed.
PANEL_TOKEN = os.environ.get("PANEL_TOKEN", "")  # optional single fixed token