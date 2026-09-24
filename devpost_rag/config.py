"""Central configuration: paths, politeness settings, LLM env vars."""
import os
from pathlib import Path

# Data lives under ./data next to the repo unless DEVPOST_RAG_DATA overrides it.
DATA_DIR = Path(os.environ.get("DEVPOST_RAG_DATA", Path.cwd() / "data"))
DB_PATH = DATA_DIR / "devpost.db"

# Politeness: one request per host every ~REQUEST_DELAY seconds (jittered),
# real browser-shaped headers because AWS WAF fingerprints plain bots.
REQUEST_DELAY = float(os.environ.get("DEVPOST_RAG_DELAY", "1.2"))
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

HACKATHON_API = "https://devpost.com/api/hackathons"
HACKATHONS_PER_PAGE = 40  # server caps per_page at 40
