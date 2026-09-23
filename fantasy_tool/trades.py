"""Trade offer builder.

A trade is judged the same way as a waiver move: the week-by-week season
simulation (byes, injuries, injury-risk depth) run on BOTH rosters, before
and after. An offer is shown only if:

- you gain at least MIN_MY_GAIN season points;
- their lineups come out even or better (so it's worth their time);
- on paper it looks fair to them: the rest-of-season points they receive
  are at least PAPER_FAIRNESS of what they give up;
- you gain more than they do.

The edge comes from positional need: a trade that's even on paper can
still help your lineup more than theirs.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from . import advisor, analysis

MIN_MY_GAIN = 3.0
PAPER_FAIRNESS = 0.9
UNTRADEABLE_POSITIONS = {"K", "D/ST"}


@dataclass
class TeamRoster:
    team: object  # espn-api Team
    players: list  # analysis.Profile
    limit: int  # max players outside IR slots


def _active(players: list) -> list:
    return [p for p in players if p.slot != "IR"]


def _fit_roster(players: list, keep: list, limit: int) -> tuple[list, list]:
    """If a trade leaves a team over its roster limit, it cuts its least
    valuable players (never the ones it just received). Returns
    (roster, players cut)."""
    active = _active(players)
    over = len(active) - limit
    if over <= 0:
        return players, []
    cuttable = sorted((p for p in active if p not in keep), key=analysis.ros_market_value)
    cut = cuttable[:over]
    return [p for p in players if p not in cut], cut


def _season(players: list, slots: list, week: int, final_week: int, risk: bool) -> float:
    return sum(analysis.season_points(players, slots, week, final_week, injury_risk=risk).values())


def find_trades(me: TeamRoster, others: list[TeamRoster], slot_counts: dict[str, int],
                week: int, final_week: int, give_filter=None, get_filter=None,
                max_per_team: int = 2, top: int = 8) -> list[dict]:
    slots = advisor.starting_slots(slot_counts)

    def my_starters_if_added(x) -> bool:
        # Screening: only chase players who'd actually start for you.
        _, starters = analysis._ros_lineup_points(me.players + [x], slots)
        return x in starters

    my_base = {False: _season(me.players, slots, week, final_week, False)}
    gives = [p for p in me.players if p.position not in UNTRADEABLE_POSITIONS and (not give_filter or give_filter(p))]
    candidates = []
    for other in others:
        their_base = {False: _season(other.players, slots, week, final_week, False)}
        gets = [x for x in other.players
                if x.position not in UNTRADEABLE_POSITIONS and x.slot != "IR"
                and (get_filter(x) if get_filter else my_starters_if_added(x))]
        for x in gets:
            packages = [(g,) for g in gives] + list(combinations(gives, 2))
            for pkg in packages:
                mine = [p for p in me.players if p not in pkg] + [x]
                theirs = [p for p in other.players if p is not x] + list(pkg)
                theirs, cut = _fit_roster(theirs, list(pkg), other.limit)
                my_gain = _season(mine, slots, week, final_week, False) - my_base[False]
                their_gain = _season(theirs, slots, week, final_week, False) - their_base[False]
                if my_gain < MIN_MY_GAIN or their_gain < -1.0 or my_gain <= their_gain:
                    continue
                paper_give = sum(analysis.ros_market_value(p) for p in pkg)
                paper_get = analysis.ros_market_value(x)
                if paper_get <= 0 or paper_give < PAPER_FAIRNESS * paper_get:
                    continue
                candidates.append({"other": other, "give": pkg, "get": (x,), "cut": cut,
                                   "screen": my_gain, "paper_give": paper_give, "paper_get": paper_get})

    # Refine the strongest screened offers with the full injury-risk model.
    candidates.sort(key=lambda c: -c["screen"])
    base_cache: dict = {}
    results = []
    for c in candidates[:60]:
        other = c["other"]
        if id(other) not in base_cache:
            base_cache[id(other)] = _season(other.players, slots, week, final_week, True)
        if "me" not in base_cache:
            base_cache["me"] = _season(me.players, slots, week, final_week, True)
        mine = [p for p in me.players if p not in c["give"]] + list(c["get"])
        theirs = [p for p in other.players if p not in c["get"]] + list(c["give"])
        theirs, cut = _fit_roster(theirs, list(c["give"]), other.limit)
        my_gain = _season(mine, slots, week, final_week, True) - base_cache["me"]
        their_gain = _season(theirs, slots, week, final_week, True) - base_cache[id(other)]
        if my_gain < MIN_MY_GAIN or their_gain < 0 or my_gain <= their_gain:
            continue
        results.append({**c, "my_gain": my_gain, "their_gain": their_gain, "cut": cut,
                        "my_open_spots": max(len(c["give"]) - len(c["get"]), 0)})

    # One package per target player: the best for you, and when two are
    # about equal (within 1 pt) the one that gives up fewer players, so a
    # throw-in that adds nothing never shows as its own offer.
    best: dict = {}
    for r in results:
        k = (id(r["other"]), tuple(id(p) for p in r["get"]))
        cur = best.get(k)
        if (cur is None or r["my_gain"] > cur["my_gain"] + 1.0
                or (abs(r["my_gain"] - cur["my_gain"]) <= 1.0 and len(r["give"]) < len(cur["give"]))):
            best[k] = r
    results = list(best.values())

    # Best for you first, a couple per team so the list isn't one owner.
    results.sort(key=lambda r: -r["my_gain"])
    per_team: dict = {}
    out = []
    for r in results:
        k = id(r["other"])
        if per_team.get(k, 0) >= max_per_team:
            continue
        per_team[k] = per_team.get(k, 0) + 1
        out.append(r)
        if len(out) >= top:
            break
    return out


def position_counts(players: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in _active(players):
        counts[p.position] = counts.get(p.position, 0) + 1
    return counts
