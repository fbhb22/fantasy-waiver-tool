from __future__ import annotations

from espn_api.football import League, Player

from . import config


def get_league(league_id: int | None = None, year: int | None = None) -> League:
    """ESPN's fantasy API is unofficial (no developer app or approval, unlike
    Yahoo). Public leagues need only the league id; private leagues also
    need the espn_s2 + SWID cookies from a logged-in fantasy.espn.com
    browser session. SWID also identifies which team is yours."""
    league_id = league_id or config.ESPN_LEAGUE_ID
    if not league_id:
        raise RuntimeError("Set ESPN_LEAGUE_ID in .env (it's the leagueId=... in your league's URL).")
    return League(
        league_id=int(league_id),
        year=int(year or config.ESPN_YEAR),
        espn_s2=config.ESPN_S2 or None,
        swid=config.ESPN_SWID or None,
    )


def get_my_team(league: League):
    """Your team, matched by SWID against each team's owner ids."""
    if not config.ESPN_SWID:
        raise RuntimeError("Set ESPN_SWID in .env so your team can be identified.")
    swid = config.ESPN_SWID.strip().lower()
    for team in league.teams:
        owner_ids = [(o.get("id") if isinstance(o, dict) else o) or "" for o in team.owners]
        if swid in (i.strip().lower() for i in owner_ids):
            return team
    raise RuntimeError("No team in this league is owned by the SWID in .env — check ESPN_SWID.")


def get_my_roster(league: League) -> list[Player]:
    return get_my_team(league).roster


def get_free_agents(league: League, position: str | None = None, size: int = 50) -> list[Player]:
    return league.free_agents(size=size, position=position)


def week_projection(player: Player, week: int) -> float:
    return player.stats.get(week, {}).get("projected_points", 0.0)
