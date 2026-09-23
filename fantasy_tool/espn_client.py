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


def get_player_pool(league: League, position: str, size: int = 20) -> list:
    """Free agents at one position, as BoxPlayers (this week's opponent and
    ESPN's opponent rank vs. the position), each with the raw ESPN
    `ownership` dict attached, which espn-api otherwise drops. That dict
    carries percentChange: the week-over-week change in % owned across all
    ESPN leagues, i.e. who's being picked up right now."""
    import json
    from espn_api.football.box_player import BoxPlayer
    from espn_api.football.constant import POSITION_MAP

    week = league.current_week
    filters = {"players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "filterSlotIds": {"value": [POSITION_MAP[position]]},
        "limit": size,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }}
    data = league.espn_request.league_get(
        params={"view": "kona_player_info", "scoringPeriodId": week},
        headers={"x-fantasy-filter": json.dumps(filters)},
    )
    # These two are espn-api internals (what free_agents() itself uses);
    # pinned to espn-api 0.46.0's behavior.
    schedule = league._get_pro_schedule(week)
    ratings = league._get_positional_ratings(week)
    out = []
    for raw in data["players"]:
        p = BoxPlayer(raw, schedule, ratings, week, league.year)
        p.ownership = raw["player"].get("ownership") or {}
        p.injury_details = raw["player"].get("injuryDetails") or {}
        out.append(p)
    return out


def get_injury_details(league: League, player_ids: list[int]) -> dict[int, dict]:
    """ESPN's injuryDetails per player ({'type', 'expectedReturnDate':
    [y, m, d], 'outForSeason'}), for players not loaded via get_player_pool()
    (e.g. your roster). One request. Healthy players have no entry."""
    import json

    if not player_ids:
        return {}
    filters = {"players": {"filterIds": {"value": list(player_ids)}}}  # a "limit" here gets an HTTP 400
    data = league.espn_request.league_get(
        # kona_playercard is what carries expectedReturnDate for rostered players.
        params={"view": ["kona_player_info", "kona_playercard"], "scoringPeriodId": league.current_week},
        headers={"x-fantasy-filter": json.dumps(filters)},
    )
    return {raw["player"]["id"]: raw["player"].get("injuryDetails") or {} for raw in data.get("players", [])}


def get_pro_schedule(league: League) -> dict[str, dict]:
    """Every NFL team's season, keyed by team abbreviation (as on Player.proTeam):
    {'bye': week, 'games': {week: kickoff datetime}}. One request."""
    import datetime as dt
    from espn_api.football.constant import PRO_TEAM_MAP

    data = league.espn_request.get_pro_schedule()
    out = {}
    for team in data.get("settings", {}).get("proTeams", []):
        abbrev = PRO_TEAM_MAP.get(team["id"], team.get("abbrev"))
        games = {}
        for wk, gs in (team.get("proGamesByScoringPeriod") or {}).items():
            if gs:
                games[int(wk)] = dt.datetime.fromtimestamp(gs[0]["date"] / 1000)
        out[abbrev] = {"bye": team.get("byeWeek"), "games": games}
    return out


def get_news(player_id: int, name: str, limit: int = 2) -> list[tuple[str, str]]:
    """Latest ESPN news blurbs about this player: [(YYYY-MM-DD, headline)].
    ESPN's per-player feed also returns general articles that merely mention
    him, so only headlines naming him are kept."""
    import requests

    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players",
            params={"playerId": player_id, "limit": 10}, timeout=15,
        )
        feed = resp.json().get("feed") or [] if resp.ok else []
    except (requests.RequestException, ValueError):
        return []
    last = name.replace(" Jr.", "").replace(" Sr.", "").split()[-1].lower()
    out = []
    for item in feed:
        headline = (item.get("headline") or "").strip()
        if last in headline.lower():
            out.append(((item.get("published") or "")[:10], headline))
        if len(out) >= limit:
            break
    return out


def get_history(league: League, player_ids: list[int]) -> dict[int, Player]:
    """Full-season, week-by-week stats for these players in one request."""
    if not player_ids:
        return {}
    result = league.player_info(playerId=list(player_ids))
    players = result if isinstance(result, list) else [result]
    return {p.playerId: p for p in players if p}


def week_projection(player: Player, week: int) -> float:
    return player.stats.get(week, {}).get("projected_points", 0.0)
