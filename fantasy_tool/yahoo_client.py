from __future__ import annotations

import json

from yahoo_oauth import OAuth2
import yahoo_fantasy_api as yfa

from . import config


def _ensure_oauth_file() -> None:
    if config.OAUTH_FILE.exists():
        return
    if not config.CLIENT_ID or not config.CLIENT_SECRET:
        raise RuntimeError(
            "Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET in .env before first run."
        )
    config.OAUTH_FILE.write_text(
        json.dumps({"consumer_key": config.CLIENT_ID, "consumer_secret": config.CLIENT_SECRET})
    )


REDIRECT_URI = "https://localhost:8080"  # must match the Redirect URI on the Yahoo app


def get_session() -> OAuth2:
    """Returns an authenticated session; the OAuth2 constructor itself
    handles refreshing an expired token. On first-ever run this opens a
    browser to Yahoo's login/approve page and prompts in the terminal: after
    approving, paste back the `code` query param from the redirected
    (non-loading) https://localhost:8080?code=... URL."""
    _ensure_oauth_file()
    return OAuth2(None, None, from_file=str(config.OAUTH_FILE), callback_uri=REDIRECT_URI)


def get_league(oauth: OAuth2 | None = None, league_key: str | None = None) -> yfa.League:
    oauth = oauth or get_session()
    league_key = league_key or config.LEAGUE_KEY
    if not league_key:
        raise RuntimeError("Set YAHOO_LEAGUE_KEY in .env (see list_leagues.py to find it).")
    game = yfa.Game(oauth, "nfl")
    return game.to_league(league_key)


def get_my_roster(league: yfa.League) -> list[dict]:
    team = league.to_team(league.team_key())
    return team.roster()


def get_free_agents(league: yfa.League, position: str | None = None) -> list[dict]:
    return league.free_agents(position)
