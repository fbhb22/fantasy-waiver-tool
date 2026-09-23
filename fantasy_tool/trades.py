"""Trade offer builder.

A trade is judged with the week-by-week season simulation (byes, injuries,
injury-risk depth, strength of schedule) run on BOTH rosters, before and
after. An offer is shown only if:

1. Your edge is robust: you gain at least MIN_MY_GAIN season points under
   every scoring view (blend / ESPN projection only / actual-heavy), not
   just the default one. Confidence is "high" if you still gain under a
   pessimistic check (players you receive at PESSIMISTIC_HAIRCUT of their
   value), "medium" otherwise.
2. They'd consider it: their lineups come out even or better, and it looks
   fair from their side in "perceived value" -- how the ESPN market rates
   the players (ESPN projection, average auction value, player rater),
   with a premium for stars so two mid players don't equal one stud.
3. You come out on top: you gain more season points than they do.

The edge comes from positional need and from gaps between how the market
rates a player (perceived value) and what he's worth to each lineup.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from . import advisor, analysis

MIN_MY_GAIN = 3.0
PERCEIVED_FAIRNESS = 0.9  # they must get >= 90% of the perceived value they give up
PESSIMISTIC_HAIRCUT = 0.9
MODES = ("blend", "espn", "actual")
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
    valuable players (never the ones it just received)."""
    active = _active(players)
    over = len(active) - limit
    if over <= 0:
        return players, []
    cuttable = sorted((p for p in active if all(p is not k for k in keep)), key=analysis.ros_market_value)
    cut = cuttable[:over]
    return [p for p in players if all(p is not c for c in cut)], cut


def _without(players: list, remove) -> list:
    return [p for p in players if all(p is not r for r in remove)]


def _season(players, slots, week, final_week, risk=True, mode="blend", haircut=None) -> float:
    return sum(analysis.season_points(players, slots, week, final_week,
                                      injury_risk=risk, mode=mode, haircut=haircut).values())


# --- perceived (market) value -------------------------------------------

class PerceivedValue:
    """How the ESPN market would size up a player, from three views: ESPN's
    rest-of-season projection, his average auction value across ESPN
    (reputation), and ESPN's player rater (production so far). Each is a
    percentile among all rostered non-K/D/ST players; the average is squared
    so value is convex -- a star is worth more than two average starters."""

    def __init__(self, players: list):
        pool = [p for p in players if p.position not in UNTRADEABLE_POSITIONS]
        self._metrics = [
            sorted(p.season_avg * len(p.available) for p in pool),
            sorted(p.auction for p in pool),
            sorted(p.espn_rating for p in pool),
        ]

    @staticmethod
    def _pct(sorted_vals: list, v: float) -> float:
        if not sorted_vals:
            return 0.0
        below = sum(1 for x in sorted_vals if x < v)
        return below / len(sorted_vals)

    def __call__(self, p) -> float:
        cache = self.__dict__.setdefault("_cache", {})
        if id(p) not in cache:
            vals = (p.season_avg * len(p.available), p.auction, p.espn_rating)
            score = sum(self._pct(m, v) for m, v in zip(self._metrics, vals)) / 3
            cache[id(p)] = round(100 * score ** 2, 1)
        return cache[id(p)]


# --- search -----------------------------------------------------------------

def find_trades(me: TeamRoster, others: list[TeamRoster], slot_counts: dict[str, int],
                week: int, final_week: int, give_filter=None, get_filter=None,
                max_per_team: int = 2, top: int = 8) -> list[dict]:
    slots = advisor.starting_slots(slot_counts)
    perceived = PerceivedValue(me.players + [p for o in others for p in o.players])

    def would_start_for_me(x) -> bool:
        _, starters = analysis._ros_lineup_points(me.players + [x], slots, week + 1)
        return any(s is x for s in starters)

    # 1) Screen fast: deterministic model, default view.
    my_base = _season(me.players, slots, week, final_week, risk=False)
    gives = [p for p in me.players if p.position not in UNTRADEABLE_POSITIONS and (not give_filter or give_filter(p))]
    candidates = []
    for other in others:
        their_base = _season(other.players, slots, week, final_week, risk=False)
        gets = [x for x in other.players
                if x.position not in UNTRADEABLE_POSITIONS and x.slot != "IR"
                and (get_filter(x) if get_filter else would_start_for_me(x))]
        for x in gets:
            for pkg in [(g,) for g in gives] + list(combinations(gives, 2)):
                pv_give, pv_get = sum(perceived(p) for p in pkg), perceived(x)
                if pv_get <= 0 or pv_give < PERCEIVED_FAIRNESS * pv_get:
                    continue  # looks unfair to them: don't bother simulating
                mine = _without(me.players, pkg) + [x]
                theirs, _ = _fit_roster(_without(other.players, [x]) + list(pkg), list(pkg), other.limit)
                my_gain = _season(mine, slots, week, final_week, risk=False) - my_base
                their_gain = _season(theirs, slots, week, final_week, risk=False) - their_base
                if my_gain < MIN_MY_GAIN or their_gain < -1.0 or my_gain <= their_gain:
                    continue
                candidates.append({"other": other, "give": pkg, "get": (x,), "screen": my_gain,
                                   "pv_give": pv_give, "pv_get": pv_get})

    # 2) Refine the strongest with the full model, under every scoring view.
    candidates.sort(key=lambda c: -c["screen"])
    base_mine = {m: _season(me.players, slots, week, final_week, mode=m) for m in MODES}
    base_theirs: dict = {}
    results = []
    for c in candidates[:80]:
        other = c["other"]
        if id(other) not in base_theirs:
            base_theirs[id(other)] = _season(other.players, slots, week, final_week)
        mine = _without(me.players, c["give"]) + list(c["get"])
        theirs, cut = _fit_roster(_without(other.players, c["get"]) + list(c["give"]), list(c["give"]), other.limit)

        gains = {m: _season(mine, slots, week, final_week, mode=m) - base_mine[m] for m in MODES}
        if min(gains.values()) < MIN_MY_GAIN:
            continue  # the edge disappears under some reasonable view
        haircut = {id(x): PESSIMISTIC_HAIRCUT for x in c["get"]}
        pessimistic = _season(mine, slots, week, final_week, haircut=haircut) - base_mine["blend"]
        their_gain = _season(theirs, slots, week, final_week) - base_theirs[id(other)]
        if their_gain < 0 or gains["blend"] <= their_gain:
            continue

        ratio = c["pv_give"] / c["pv_get"]
        results.append({
            **c, "cut": cut, "my_gain": gains["blend"], "gain_range": (min(gains.values()), max(gains.values())),
            "confidence": "high" if pessimistic >= MIN_MY_GAIN else "medium",
            "their_gain": their_gain, "perceived_ratio": ratio,
            "acceptance": "likely" if ratio >= 1.0 and their_gain >= 5 else "possible",
            "their_upgrades": _lineup_upgrades(other.players, theirs, list(c["give"]), slots, week),
            "my_open_spots": max(len(c["give"]) - len(c["get"]), 0),
        })

    # One package per target: best for you; within 1 pt prefer fewer players.
    best: dict = {}
    for r in results:
        k = (id(r["other"]), tuple(id(p) for p in r["get"]))
        cur = best.get(k)
        if (cur is None or r["my_gain"] > cur["my_gain"] + 1.0
                or (abs(r["my_gain"] - cur["my_gain"]) <= 1.0 and len(r["give"]) < len(cur["give"]))):
            best[k] = r

    # Rank by your worst-case gain (the robust number), a couple per team.
    ranked = sorted(best.values(), key=lambda r: (r["confidence"] != "high", -r["gain_range"][0]))
    per_team: dict = {}
    out = []
    for r in ranked:
        k = id(r["other"])
        if per_team.get(k, 0) >= max_per_team:
            continue
        per_team[k] = per_team.get(k, 0) + 1
        out.append(r)
        if len(out) >= top:
            break
    return out


def _lineup_upgrades(before: list, after: list, incoming: list, slots: list, week: int) -> list:
    """Players you send who'd start for the other team in the next week they
    can play: the concrete 'what's in it for them'."""
    out = []
    for p in incoming:
        wk = min((w for w in p.available if w > week), default=None)
        if wk is None:
            continue
        _, starters = analysis._ros_lineup_points([q for q in after if wk in q.available], slots, wk)
        if any(s is p for s in starters):
            out.append(p)
    return out


def position_counts(players: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in _active(players):
        counts[p.position] = counts.get(p.position, 0) + 1
    return counts
