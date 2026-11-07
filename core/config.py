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
MAX_REQUEST_BYTES = int(os.environ.get('PANEL_MAX_REQUEST_BYTES', '1048576'))
if not 1 <= MAX_REQUEST_BYTES <= 1048576:
    raise ValueError('PANEL_MAX_REQUEST_BYTES must be between 1 and 1048576')

# ---------------------------------------------------------------- pacing
# ponytail: intentionally one process, one worker, one operator, one stable uid.
# Pacing is not a quota workaround; refusals are returned without automatic retry.
PACE_MIN_INTERVAL = max(5.0, float(os.environ.get("PANEL_PACE", "5.0")))
PACE_MAX_INTERVAL = PACE_MIN_INTERVAL  # compatibility; no adaptive acceleration
RATE_LIMIT_COOLDOWN = 60.0
MAX_RETRY_AFTER = 3600.0
REQUEST_TIMEOUT = 180.0

# ---------------------------------------------------------------- panel auth
# Local API keys for the OpenAI-compatible shim. Stored hashed.
PANEL_TOKEN = os.environ.get("PANEL_TOKEN", "")  # optional single fixed token