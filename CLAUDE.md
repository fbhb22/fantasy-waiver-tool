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
- Installing `espn-api` downgraded `urllib3` to 2.2.3 (its pin); the Yahoo libraries still import fine.
