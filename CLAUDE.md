# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Setup (one-time)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# fill in YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET in .env from https://developer.yahoo.com/apps/

# Find your league key (first run triggers the OAuth flow, see below)
.venv\Scripts\python.exe list_leagues.py
# put the printed key into .env as YAHOO_LEAGUE_KEY

# Show current roster + free agents
.venv\Scripts\python.exe main.py yahoo
.venv\Scripts\python.exe main.py espn
```

There is no automated test suite in this repo.

**The OAuth flow needs a real interactive terminal.** On first run (or whenever the cached token in `oauth2.json` is invalid), `yahoo_oauth.OAuth2` opens a browser to Yahoo's login/consent page and then blocks on `input("Enter verifier : ")` in the terminal. This means `list_leagues.py`/`main.py` cannot be run through a non-interactive automation shell — the `input()` call fails with `EOFError` there. Always run these two scripts from a real terminal window. After approving access, Yahoo redirects to `https://localhost:8080/?code=...`, which won't actually load (expected, nothing is listening there) — copy the `code` value from the address bar and paste it at the prompt.

## Architecture

- `fantasy_tool/config.py` loads `.env` and defines `OAUTH_FILE` (`oauth2.json` in the project root) — this single file is both read and rewritten by `yahoo_oauth`, holding the consumer key/secret *and* the current access/refresh token together (gitignored).
- `fantasy_tool/yahoo_client.py` wraps `yahoo_oauth.OAuth2` + `yahoo_fantasy_api`: `get_session()` handles auth (including token refresh — `OAuth2`'s constructor does this internally, so callers never need to check token validity themselves), `get_league()` resolves a `yfa.League` from `YAHOO_LEAGUE_KEY`, and `get_my_roster()`/`get_free_agents()` are thin wrappers over the library's `Team.roster()` / `League.free_agents()`.
- `REDIRECT_URI` in `yahoo_client.py` is hardcoded to `https://localhost:8080` and must match the Redirect URI configured on the Yahoo app — `yahoo_oauth` otherwise defaults to `'oob'`, which would mismatch.
- **Yahoo API access is approval-gated** (self-serve Fantasy Sports API access was shut down 2026-07-22): a working Client ID requires (1) applying at sports.yahoo.com/developer/access/, (2) signing Yahoo's DocuSign API Access and Use Agreement, and (3) submitting the Developer Application Confirmation Form with that Client ID — checking "Fantasy Sports - Read" at app-creation time alone is not sufficient to activate access. A `"This application is not authorized to perform this action"` error means this provisioning chain isn't complete yet for the Client ID in use, not a code bug.
- `.env.example` is the committed template (blank placeholders only) and is pushed to the public GitHub repo (https://github.com/fbhb22/fantasy-waiver-tool) — real credentials belong only in the gitignored `.env`. Double-check `git status`/diff before any commit on this repo given real secrets have accidentally been typed into `.env.example` once before.
- `fantasy_tool/espn_client.py` (added 2026-09-23) wraps the open-source `espn-api` package (ESPN's API is unofficial/undocumented; no developer app or approval). Config: `ESPN_LEAGUE_ID`, `ESPN_YEAR`, and for private leagues the `ESPN_S2` + `ESPN_SWID` browser cookies. `get_my_team()` matches your team by SWID against `team.owners`. Weekly projections live in `player.stats[week]['projected_points']` (`week_projection()`). Unlike Yahoo, no interactive OAuth: ESPN scripts run fine in a non-interactive shell. The ESPN cookies are credentials: `.env` only.
- `fantasy_tool/advisor.py` + `advise.py` (2026-09-23): platform-agnostic start/sit and waiver logic on `RosterPlayer` records (`espn_player()` is the ESPN adapter; Yahoo needs its own). Best lineup = greedy fill by this week's projection, single-position slots first then flex; players projected 0 never fill a slot. Waiver gain = best-lineup points with the free agent added minus without; the suggested drop is the bench player with the lowest season-average projection. Read-only, never changes the roster.
- `fantasy_tool/analysis.py` + `waivers.py` (2026-09-23): deeper pickup/drop + FAAB analysis.
  - **ROS value:** `Profile.ros_ppg` = ESPN season projection blended toward actual PPG; the weight on actual grows to 40% by 6 games. A pickup's value = best-lineup gain in ros_ppg × games left.
  - **Drops:** chosen per add via `drop_ranking(roster + [fa])` (lineup loss, ros_ppg, only-depth-at-position penalty).
  - **FAAB:** `faab_bid()` maps ROS points to the league's own winning-bid distribution (median/90th pct of paid bids from `recent_activity`). Demand is raised by ESPN `percentChange`, capped at 35% of remaining budget and at the richest rival + $1.
  - **Data:** `espn_client.get_player_pool()` uses espn-api internals (`_get_pro_schedule`, `_get_positional_ratings`) to build BoxPlayers plus the raw `ownership` dict. Opp rank: 1 = toughest, verified against ESPN's avg points allowed. `get_history()` uses one batched `player_info` call.
  - **Gotcha:** "D/ST" contains a slash but isn't a flex; always use `advisor.is_flex()`.
  - **Byes + injuries (2026-09-23):** value is a week-by-week simulation (`season_points`): each week's best lineup from players available that week. The current week uses ESPN's weekly projection; later weeks use `ros_ppg`.
    - `set_availability()` uses `get_pro_schedule()` (one request: every team's bye and kickoff per week) and ESPN `injuryDetails.expectedReturnDate` / `outForSeason`.
    - For rostered players, `get_injury_details()` needs the `kona_playercard` view, and adding a `limit` to its `filterIds` filter gets an HTTP 400.
    - Status OUT/DOUBTFUL/IR/SUSPENSION always removes the current week, even when the return date is game day (Monday night).
    - `get_news()` hits site.api.espn.com and keeps only headlines naming the player.
  - **Injury risk / depth value (2026-09-23):** future weeks in `season_points()` subtract, for each starter, `WEEKLY_MISS_RATE[pos]` (RB 8%, WR/TE 6%, QB 4%; rough ballpark figures, not fitted) x the points lost without him. This first-order expected value ignores two starters missing the same week. It gives bench depth value by position, so a 4th RB outranks a 7th WR. `injury_risk=False` gives the old deterministic model.
  - `waivers.py` takes ~1.5 min: roughly 12 ESPN requests + news, plus the simulation. BoxPlayer `injuryStatus` can be a list; `advisor._injury_status()` normalizes it.
  - **Known gaps:** ESPN's return date can lag the news (Goedert: date 9/28 vs. "miss a few weeks"), so read the headline. Future weeks use a flat per-game value, not matchup-adjusted.
- Installing `espn-api` downgraded `urllib3` to 2.2.3 (its pin); the Yahoo libraries still import fine.
