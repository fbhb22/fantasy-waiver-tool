from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
import os

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

CLIENT_ID = os.environ.get("YAHOO_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("YAHOO_CLIENT_SECRET", "")
LEAGUE_KEY = os.environ.get("YAHOO_LEAGUE_KEY", "")

# yahoo_oauth reads/writes consumer key, secret, and token data all in this
# one file - it must exist with at least consumer_key/consumer_secret before
# the first OAuth2(...) call.
OAUTH_FILE = PROJECT_DIR / "oauth2.json"
