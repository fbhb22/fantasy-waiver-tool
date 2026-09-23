"""Deeper pickup/drop analysis and FAAB bid sizing.

Value model, in one line: a player's rest-of-season (ROS) worth is his
blended points per game (ESPN's season projection, pulled toward what he's
actually scored once he has 2+ games), and a pickup's value is how much it
raises your best ROS lineup, times the games left.

Everything is a heuristic meant to be read, not obeyed: each number the
report prints is shown next to the signals behind it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from . import advisor

# Kickers and defenses are streamed week to week: judged on this week only,
# and never worth more than a token bid.
STREAM_POSITIONS = {"K", "D/ST"}

# Weight on actual scoring vs. ESPN's projection grows with games played,
# reaching ACTUAL_WEIGHT_MAX at FULL_WEIGHT_GAMES, so a 2-game hot streak
# only nudges the projection.
ACTUAL_WEIGHT_MAX = 0.4
FULL_WEIGHT_GAMES = 6

# Opportunity = targets + carries (QBs: pass attempts + carries).
_OPP_KEYS = ("receivingTargets", "rushingAttempts")
_QB_OPP_KEYS = ("passingAttempts", "rushingAttempts")


@dataclass
class Profile(advisor.RosterPlayer):
    player_id: int = 0
    games: int = 0
    actual_ppg: float = 0.0
    last_points: float | None = None
    opps_per_game: float | None = None
    last_opps: float | None = None
    trend: str = ""  # "rising" / "falling" / ""
    pct_change: float = 0.0  # week-over-week change in % owned, all ESPN leagues
    opponent: str = ""
    opp_rank: int = 0  # ESPN rank vs. this position: 1 = toughest, 32 = easiest

    @property
    def ros_ppg(self) -> float:
        if self.games >= 2 and self.season_avg > 0:
            w = ACTUAL_WEIGHT_MAX * min(self.games, FULL_WEIGHT_GAMES) / FULL_WEIGHT_GAMES
            return round((1 - w) * self.season_avg + w * self.actual_ppg, 2)
        return self.season_avg


def build_profile(p, history, week: int) -> Profile:
    """`p` is the player as loaded this week (roster Player or BoxPlayer);
    `history` is the same player from get_history() (all weeks), or None."""
    base = advisor.espn_player(p, week)
    prof = Profile(**{k: getattr(base, k) for k in base.__dataclass_fields__})
    prof.player_id = p.playerId
    prof.pct_change = (getattr(p, "ownership", None) or {}).get("percentChange", 0.0) or 0.0
    prof.opponent = getattr(p, "pro_opponent", "") or ""
    prof.opp_rank = getattr(p, "pro_pos_rank", 0) or 0

    stats = (history or p).stats
    keys = _QB_OPP_KEYS if prof.position == "QB" else _OPP_KEYS
    played = []
    for wk in sorted(k for k in stats if 0 < k < week):
        s = stats[wk]
        if "points" in s and s.get("breakdown"):
            opps = sum(s["breakdown"].get(k, 0) or 0 for k in keys)
            played.append((s["points"], opps))
    prof.games = len(played)
    if played:
        prof.actual_ppg = round(sum(pt for pt, _ in played) / len(played), 2)
        prof.last_points = played[-1][0]
        if prof.position not in STREAM_POSITIONS:
            prof.opps_per_game = round(sum(o for _, o in played) / len(played), 1)
            prof.last_opps = played[-1][1]
            if len(played) >= 2:
                prior = sum(o for _, o in played[:-1]) / (len(played) - 1)
                last = played[-1][1]
                if last - prior >= 2 and last >= 1.25 * prior:
                    prof.trend = "rising"
                elif prior - last >= 2 and last <= 0.75 * prior:
                    prof.trend = "falling"
    return prof


def _ros_points(lineup_players: list[Profile], slots: list[str]) -> float:
    lineup = advisor.best_lineup(lineup_players, slots, key=lambda p: p.ros_ppg)
    return sum(p.ros_ppg for p in lineup.values() if p)


def drop_ranking(roster: list[Profile], slot_counts: dict[str, int]) -> list[dict]:
    """Roster players ordered from most to least droppable, by how much the
    best ROS lineup loses without them (almost always 0 for bench players),
    then ROS points per game. A player who is your only depth at a position
    you start is ranked as less droppable, so a drop never leaves a slot
    with nobody to cover a bye."""
    slots = advisor.starting_slots(slot_counts)
    full = _ros_points(roster, slots)
    need = {s: n for s, n in slot_counts.items() if not advisor.is_flex(s) and s not in advisor.NON_STARTING_SLOTS}
    by_pos: dict[str, int] = {}
    for p in roster:
        by_pos[p.position] = by_pos.get(p.position, 0) + 1

    out = []
    for p in roster:
        loss = full - _ros_points([q for q in roster if q is not p], slots)
        reasons = []
        only_depth = by_pos.get(p.position, 0) <= need.get(p.position, 0)
        if only_depth:
            reasons.append(f"your only {p.position}")
        if loss > 0.05:
            reasons.append(f"best ROS lineup loses {loss:.1f}/game")
        if p.injury:
            reasons.append(p.injury.lower())
        if p.trend == "falling":
            reasons.append("usage falling")
        score = loss * 10 + p.ros_ppg + (5 if only_depth else 0)
        out.append({"player": p, "loss": loss, "score": score, "reasons": reasons})
    out.sort(key=lambda d: d["score"])
    return out


# --- FAAB ---------------------------------------------------------------

@dataclass
class Market:
    budget: int
    my_remaining: int
    rival_remaining: list[int]  # other teams' remaining budgets, high to low
    winning_bids: list[int] = field(default_factory=list)  # this league's past winning bids

    @property
    def typical(self) -> float:
        paid = [b for b in self.winning_bids if b > 0]
        return statistics.median(paid) if paid else 1.0

    @property
    def high(self) -> float:
        paid = sorted(b for b in self.winning_bids if b > 0)
        if not paid:
            return 5.0
        # 90th percentile, so one preseason splurge doesn't set the scale.
        return paid[min(len(paid) - 1, int(round(0.9 * (len(paid) - 1))))]


def faab_bid(ros_gain_points: float, pct_change: float, market: Market, stream: bool = False) -> tuple[int, int]:
    """(fair, to_win) bid in dollars.

    - fair: scales from $0 (no ROS gain) through the league's typical winning
      bid, up to its high end for a pickup worth ~60 ROS points (about 5 points
      a week over the rest of the season), and beyond that for more.
    - to_win: fair, raised when the player is being added fast across ESPN
      (competition), but never more than the richest rival could bid + $1.
    Both are capped at 35% of your remaining budget, so one bid in September
    can't wipe out the rest of the season."""
    if stream or ros_gain_points <= 0 or market.my_remaining <= 0:
        return (0, 1 if (ros_gain_points > 0 and market.my_remaining > 0) else 0)
    ratio = ros_gain_points / 60.0
    if ratio <= 1:
        fair = ratio * market.high
        if ratio < 0.25:
            fair = min(fair, market.typical)
    else:
        fair = market.high * (1 + 0.5 * (ratio - 1))
    demand = 1.0
    if pct_change >= 15:
        demand = 2.0
    elif pct_change >= 5:
        demand = 1.5
    to_win = fair * demand + 1
    cap = max(1, int(0.35 * market.my_remaining))
    richest = market.rival_remaining[0] if market.rival_remaining else market.budget
    fair = int(round(min(fair, cap, market.my_remaining)))
    to_win = int(round(min(to_win, cap, richest + 1, market.my_remaining)))
    return fair, max(to_win, fair)


def evaluate_pickups(roster: list[Profile], pool: list[Profile], slot_counts: dict[str, int],
                     games_left: int, market: Market) -> list[dict]:
    """Every free agent that improves your best ROS lineup (or, for K/D/ST,
    this week's lineup), with the best drop for that specific add and a bid."""
    slots = advisor.starting_slots(slot_counts)
    base_ros = _ros_points(roster, slots)
    base_week = advisor.lineup_points(advisor.best_lineup(roster, slots))
    out = []
    for fa in pool:
        # Rank drops as if the new player were already on the roster, so
        # e.g. adding a TE frees up your old injured TE as a drop option.
        drops = [d for d in drop_ranking(roster + [fa], slot_counts) if d["player"] is not fa]
        drop = drops[0]
        after = [p for p in roster if p is not drop["player"]] + [fa]
        ros_gain = _ros_points(after, slots) - base_ros
        week_gain = advisor.lineup_points(advisor.best_lineup(after, slots)) - base_week
        stream = fa.position in STREAM_POSITIONS
        if ros_gain * games_left <= 0.05 and week_gain <= 0.05:
            continue
        # Bids follow ROS value; a this-week-only fix (a stream, or a
        # fill-in while a starter is hurt) is worth a token $0-1.
        fair, to_win = faab_bid(0 if stream else max(ros_gain * games_left, 0), fa.pct_change, market,
                                stream=stream or ros_gain * games_left <= 0.05)
        out.append({
            "add": fa, "drop": drop, "stream": stream,
            "ros_gain_ppg": round(ros_gain, 2), "ros_gain_points": round(ros_gain * games_left, 1),
            "week_gain": round(week_gain, 1), "fair": fair, "to_win": to_win,
            "short_term": not stream and ros_gain * games_left <= 0.05,
        })
    out.sort(key=lambda r: (r["stream"], r["short_term"],
                            -(r["week_gain"] if (r["stream"] or r["short_term"]) else r["ros_gain_points"])))
    return out


def league_winning_bids(league) -> list[int]:
    """Winning FAAB bids from the league's recent activity."""
    bids = []
    for act in league.recent_activity(size=100):
        for team, action, player, *rest in act.actions:
            if action == "WAIVER ADDED" and rest and isinstance(rest[0], (int, float)):
                bids.append(int(rest[0]))
    return bids
