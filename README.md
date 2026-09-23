# fantasy-waiver-tool

Roster/waiver-wire helper for fantasy football leagues on ESPN and Yahoo.

## ESPN setup

ESPN's fantasy API is unofficial: no developer app or approval needed.

1. `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt` (skip if already done).
2. Copy `.env.example` to `.env` if you haven't, then fill in the ESPN section:
   - `ESPN_LEAGUE_ID`: the `leagueId=...` number in your league's fantasy.espn.com URL.
   - `ESPN_S2` and `ESPN_SWID`: while logged in at fantasy.espn.com, open Chrome DevTools > Application > Cookies > `https://fantasy.espn.com` and copy the `espn_s2` and `SWID` values (SWID includes the `{braces}`). Needed for private leagues, and SWID is how the tool finds your team. Treat both like a password: they belong only in `.env`, never `.env.example`.
3. Run `.venv\Scripts\python.exe main.py espn` to see your roster and the top free agents by this week's projection.
4. Run `.venv\Scripts\python.exe advise.py espn` for this week's lineup check and waiver pickups. It flags empty or injured starting slots, bench players who should start, and the free agents that would most improve your best lineup (with a suggested drop). It only reads your league; make any moves in the ESPN app.
5. Run `.venv\Scripts\python.exe waivers.py espn` for the deeper pickup/drop analysis with FAAB bids. For each candidate it shows rest-of-season points added to your best lineup, usage trend (targets + carries), ESPN ownership change, and this week's matchup. It also suggests a fair bid and a to-win bid, sized from your league's own past winning bids, your remaining budget and rivals' budgets. Short-term fill-ins and K/D/ST streams are listed separately at $0-1.

## Yahoo setup

1. Create a Yahoo developer app at https://developer.yahoo.com/apps/create/
   - Application Type: Confidential Client
   - Redirect URI: `https://localhost:8080`
   - API Permissions: Fantasy Sports -> Read
2. Copy `.env.example` to `.env` and fill in `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` from that app.
3. `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`
4. Run `.venv\Scripts\python.exe list_leagues.py` to find your league key, then put it in `.env` as `YAHOO_LEAGUE_KEY`.
   - First run will print a Yahoo login URL in the terminal - open it, approve access, then paste the `code` value from the redirected `https://localhost:8080?code=...` URL back into the terminal when prompted. The page itself won't load; that's expected, the code is in the address bar.
5. Run `.venv\Scripts\python.exe main.py yahoo` to see your current roster and available free agents.
