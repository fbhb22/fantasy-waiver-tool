"""Teammate injuries: carries ESPN's own reaction to them into later weeks.

When a team's key player is out, ESPN's weekly projections for his teammates
move: the backup RB/WR/TE gets more of the work, receivers lose a little when
the QB is out, a backup QB jumps. The model already uses ESPN's weekly
projection for the current week, so this week is covered. Later weeks are
valued from the season projection, which has no link between teammates --
so a fill-in for a multi-week absence is undervalued, and a receiver whose
QB is out for a month is overvalued.

The fix measures rather than guesses. For each active teammate of an
injured key player, the "boost" is how far ESPN's projection for this week
sits from what his season projection and this week's matchup would predict,
after removing the gap every player shows (weekly projections run a bit above
season per-game across the board; that part is measured on teams with no key
player out). The boost, in points per game, is then added to each later week
the injured player is still expected to miss (ESPN's return date), and to no
other week. ESPN has already decided who the real backup is and how the work
gets shared out; this just stops the model forgetting it after one week.

Two filters keep noise out, since a weekly projection also moves with things
this doesn't model (weather, game script, a matchup the strength-of-schedule
factor misses):
- Football sense (AFFECTS): each kind of absence can only move certain
  teammates, in one direction. A WR out can't make his QB better; a QB out
  can't help his receivers.
- Size: on teams with nobody key out, the same measure has a spread of
  roughly +/-15-20% of a player's expected points. A boost must clear
  NOISE_SDS of that spread (so a star needs a bigger swing than a depth
  guy) and MIN_BOOST_POINTS.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from . import analysis

# ESPN defaultPositionId -> position, and slot ids for the player filter.
_POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
_SLOT_IDS = [0, 2, 4, 6]  # QB, RB, WR, TE

# How many players at each position count as a team's key players, by ESPN
# season projection. Losing one of these reshapes the offense; losing a WR3
# mostly doesn't.
KEY_DEPTH = {"QB": 1, "RB": 1, "WR": 2, "TE": 1}

# Which teammates each kind of absence moves, and which way (+1: only a rise
# counts, -1: only a drop). Anything not listed is treated as noise.
AFFECTS = {
    "QB": {"QB": +1, "RB": -1, "WR": -1, "TE": -1},  # backup QB starts; the offense gets worse
    "RB": {"RB": +1},                                 # carries go to the other backs
    "WR": {"WR": +1, "TE": +1},                       # targets spread to the other receivers
    "TE": {"TE": +1, "WR": +1},
}

# Players below this season projection (points/game) are left out of the
# baseline: tiny denominators make their ratios meaningless.
BASELINE_MIN_PPG = 3.0

# A boost counts only if it's at least this many points AND this many
# standard deviations of the healthy-team spread.
MIN_BOOST_POINTS = 1.0
NOISE_SDS = 1.5

POOL_SIZE = 700  # most-owned QB/RB/WR/TE league-wide; covers every depth chart that matters


@dataclass
class Adjustment:
    boost: float  # points per game at a neutral matchup
    weeks: list[int]  # later weeks it applies to
    because: list[str]  # the injured key players behind it


@dataclass
class TeamContext:
    week: int
    by_player: dict[int, Adjustment] = field(default_factory=dict)
    noise: float = 0.0  # healthy-team spread of the boost, as a fraction of expected points
    baseline: dict[str, float] = field(default_factory=dict)  # position -> weekly/season ratio

    def apply(self, prof: analysis.Profile) -> None:
        adj = self.by_player.get(prof.player_id)
        if not adj:
            return
        prof.team_boost = {w: adj.boost for w in adj.weeks}
        who = ", ".join(adj.because)
        prof.team_note = f"{adj.boost:+.1f}/g while {who} out (wk {_weeks_str(adj.weeks)})"


def _weeks_str(weeks: list[int]) -> str:
    out, run = [], []
    for w in sorted(weeks):
        if run and w != run[-1] + 1:
            out.append(run)
            run = []
        run.append(w)
    if run:
        out.append(run)
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in out)


@dataclass
class _Row:
    player_id: int
    name: str
    team: str
    position: str
    status: str | None
    week_proj: float | None
    season_avg: float
    matchup: float = 1.0


def _load_rows(league, week: int) -> list[_Row]:
    from espn_api.football.constant import PRO_TEAM_MAP

    filters = {"players": {
        "filterSlotIds": {"value": _SLOT_IDS},
        "limit": POOL_SIZE,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }}
    data = league.espn_request.league_get(
        params={"view": "kona_player_info", "scoringPeriodId": week},
        headers={"x-fantasy-filter": json.dumps(filters)},
    )
    rows = []
    for raw in data.get("players", []):
        pl = raw["player"]
        pos = _POSITIONS.get(pl.get("defaultPositionId"))
        if not pos:
            continue
        week_proj, season_avg = None, 0.0
        for s in pl.get("stats", []):
            if s.get("statSourceId") != 1:  # projections only
                continue
            if s.get("statSplitTypeId") == 1 and s.get("scoringPeriodId") == week:
                week_proj = s.get("appliedTotal")
            elif s.get("statSplitTypeId") == 0 and s.get("seasonId") == league.year:
                season_avg = s.get("appliedAverage") or 0.0
        status = pl.get("injuryStatus")
        rows.append(_Row(
            player_id=pl["id"], name=pl.get("fullName", ""),
            team=PRO_TEAM_MAP.get(pl.get("proTeamId"), "?"), position=pos,
            status=status if status not in (None, "", "ACTIVE", "NORMAL") else None,
            week_proj=week_proj, season_avg=season_avg,
        ))
    return rows


def _stub(row: _Row) -> analysis.Profile:
    return analysis.Profile(name=row.name, position=row.position, team=row.team, eligible_slots=[],
                            proj=row.week_proj or 0.0, season_avg=row.season_avg, injury=row.status,
                            player_id=row.player_id)


def build(league, week: int, final_week: int, pro_schedule: dict, ratings: dict | None) -> TeamContext:
    """Two requests: the league-wide player pool, plus return dates for the
    injured key players."""
    from . import espn_client as espn

    rows = [r for r in _load_rows(league, week) if r.team != "?"]
    if ratings:
        for r in rows:
            stub = _stub(r)
            analysis.set_matchups(stub, pro_schedule, ratings, week, week)
            r.matchup = stub.matchup.get(week, 1.0)

    teams: dict[str, list[_Row]] = {}
    for r in rows:
        teams.setdefault(r.team, []).append(r)

    # Key players who are out, per team.
    out_keys: dict[str, list[_Row]] = {}
    for team, grp in teams.items():
        for pos, n in KEY_DEPTH.items():
            at_pos = sorted((r for r in grp if r.position == pos), key=lambda r: -r.season_avg)
            out_keys.setdefault(team, []).extend(
                r for r in at_pos[:n] if r.status in analysis._MISSING_STATUSES)
    out_keys = {t: v for t, v in out_keys.items() if v}

    ctx = TeamContext(week=week)

    # Baseline: weekly projection vs. season-per-game x matchup, per position,
    # on teams with no key player out.
    ratios: dict[str, list[float]] = {}
    healthy = []
    for team, grp in teams.items():
        if team in out_keys:
            continue
        for r in grp:
            if r.status or r.week_proj is None or r.season_avg < BASELINE_MIN_PPG:
                continue
            ratios.setdefault(r.position, []).append(r.week_proj / (r.season_avg * r.matchup))
            healthy.append(r)
    ctx.baseline = {pos: statistics.median(v) for pos, v in ratios.items() if v}

    def expected(r: _Row) -> float:
        return r.season_avg * r.matchup * ctx.baseline.get(r.position, 1.0)

    spread = [r.week_proj / expected(r) - 1 for r in healthy]
    ctx.noise = statistics.pstdev(spread) if len(spread) > 1 else 0.0

    # How long each injured key player is out, from ESPN's return date.
    injured = [r for grp in out_keys.values() for r in grp]
    meta = espn.get_player_meta(league, [r.player_id for r in injured])
    missed: dict[int, list[int]] = {}
    for r in injured:
        stub = _stub(r)
        analysis.set_availability(stub, (meta.get(r.player_id) or {}).get("injury"),
                                  pro_schedule, week, final_week)
        missed[r.player_id] = [w for w in stub.weeks_missed if w > week]

    for team, keys in out_keys.items():
        for r in teams[team]:
            if r in keys or r.status in analysis._MISSING_STATUSES or r.week_proj is None:
                continue
            if r.position not in ctx.baseline:
                continue
            # Only the absences that can plausibly move this player, and only
            # those still out after this week (this week is ESPN's already).
            causes = [k for k in keys
                      if r.position in AFFECTS[k.position] and missed.get(k.player_id)]
            if not causes:
                continue
            exp = expected(r)
            b = r.week_proj - exp
            if not any(b * AFFECTS[k.position][r.position] > 0 for k in causes):
                continue  # wrong direction for every cause: noise
            causes = [k for k in causes if b * AFFECTS[k.position][r.position] > 0]
            # Backups with a near-zero season projection (a QB2) have no
            # meaningful percentage; judge them on points alone.
            noise_pts = NOISE_SDS * ctx.noise * max(exp, BASELINE_MIN_PPG)
            if abs(b) < max(MIN_BOOST_POINTS, noise_pts):
                continue
            # With two causes on different timelines this keeps the boost
            # until the last one is back -- slightly generous in between.
            weeks = sorted({w for k in causes for w in missed[k.player_id]})
            ctx.by_player[r.player_id] = Adjustment(
                boost=round(b / r.matchup, 2),  # back to a neutral matchup
                weeks=weeks,
                because=[f"{k.name} ({k.position})" for k in causes],
            )
    return ctx
