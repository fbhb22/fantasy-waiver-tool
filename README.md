# fantasy-waiver-tool

Roster/waiver-wire helper for a Yahoo fantasy football league.

## Setup

1. Create a Yahoo developer app at https://developer.yahoo.com/apps/create/
   - Application Type: Confidential Client
   - Redirect URI: `https://localhost:8080`
   - API Permissions: Fantasy Sports -> Read
2. Copy `.env.example` to `.env` and fill in `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` from that app.
3. `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`
4. Run `.venv\Scripts\python.exe list_leagues.py` to find your league key, then put it in `.env` as `YAHOO_LEAGUE_KEY`.
   - First run will print a Yahoo login URL in the terminal - open it, approve access, then paste the `code` value from the redirected `https://localhost:8080?code=...` URL back into the terminal when prompted. The page itself won't load; that's expected, the code is in the address bar.
5. Run `.venv\Scripts\python.exe main.py` to see your current roster and available free agents.
