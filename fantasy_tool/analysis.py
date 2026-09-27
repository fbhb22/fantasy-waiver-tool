"""Deeper pickup/drop analysis and FAAB bid sizing.

Value model: a player's worth per game is his blended points per game
(ESPN's season projection, pulled toward what he's actually scored as games
accumulate). A pickup's value is simulated week by week: for this week (ESPN's
weekly projection) and every remaining week through the fantasy playoffs,
your best lineup from the players actually available that week -- not on
bye, not out injured (from ESPN's expected return date) -- with and without
the move. The difference, summed, is the points the move adds.

Everything is a heuristic meant to be read, not obeyed: each number the
report prints is shown next to the signals behind it.
"""
from __future__ import annotations

import datetime as dt
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
    bye_week: int | None = None
    injury_type: str = ""
    return_date: dt.date | None = None  # ESPN's expected return date
    out_for_season: bool = False
    available: set = field(default_factory=set)  # remaining weeks he can play
    weeks_missed: list = field(default_factory=list)  # remaining game weeks he'll miss (injury)
    matchup: dict = field(default_factory=dict)  # week -> strength-of-schedule multiplier
    opponents: dict = field(default_factory=dict)  # week -> NFL opponent
    auction: float = 0.0  # ESPN-wide average auction value (name value)
    espn_rating: float = 0.0  # ESPN player-rater score, season to date
    team_boost: dict = field(default_factory=dict)  # week -> points/game from a teammate's absence (team_context)
    team_note: str = ""

    @property
    def ros_ppg(self) -> float:
        return self.ppg("blend")

    def ppg(self, mode: str = "blend") -> float:
        """Per-game value under one of the scoring views:
        - blend: ESPN season projection pulled toward actual scoring (default)
        - espn: ESPN's season projection alone
        - actual: weighted mostly toward actual scoring (60%) once 2+ games
        Trades are checked under all three so an edge that only exists in
        one view (e.g. a 2-game hot streak) is flagged as fragile."""
        if self.games < 2 or self.season_avg <= 0 or mode == "espn":
            return self.season_avg
        if mode == "actual":
            return round(0.4 * self.season_avg + 0.6 * self.actual_ppg, 2)
        w = ACTUAL_WEIGHT_MAX * min(self.games, FULL_WEIGHT_GAMES) / FULL_WEIGHT_GAMES
        return round((1 - w) * self.season_avg + w * self.actual_ppg, 2)


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


# Rough chance a currently healthy starter misses any given future week to a
# new injury, by position. Rough NFL-wide ballpark figures, not a fitted
# model: RBs get hurt most, kickers and defenses essentially never "miss".
# This is what gives bench depth value: a backup is worth the points he'd
# save you in the weeks a starter goes down.
WEEKLY_MISS_RATE = {"QB": 0.04, "RB": 0.08, "WR": 0.06, "TE": 0.06, "K": 0.0, "D/ST": 0.0}

# Statuses that mean "misses this week" when ESPN gives no return date.
_MISSING_STATUSES = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}


def set_availability(prof: Profile, injury_details: dict | None, pro_schedule: dict,
                     week: int, final_week: int) -> None:
    """Fills bye_week / injury fields and the set of remaining weeks
    (week..final_week) the player can actually play."""
    team = pro_schedule.get(prof.team) or {}
    games = team.get("games", {})
    prof.bye_week = team.get("bye")
    game_weeks = [w for w in range(week, final_week + 1) if w in games]

    details = injury_details or {}
    prof.injury_type = details.get("type") or ""
    prof.out_for_season = bool(details.get("outForSeason"))
    ret = details.get("expectedReturnDate")
    prof.return_date = dt.date(*ret) if ret and len(ret) == 3 else None

    if prof.out_for_season:
        available = set()
    elif prof.return_date:
        available = {w for w in game_weeks if games[w].date() >= prof.return_date}
    else:
        available = set(game_weeks)
    # The current status wins for this week, even if the return date falls
    # on game day (e.g. a Monday-night game); with no return date, the
    # length is unknown, so assume this week only.
    if prof.injury in _MISSING_STATUSES:
        available.discard(week)
    prof.available = available
    prof.weeks_missed = [w for w in game_weeks if w not in available]


# Strength of schedule: how far a defense's points-allowed-per-game to a
# position is trusted after N games (N / (N + SOS_PRIOR_GAMES)), and the
# largest boost/penalty any single matchup can apply.
SOS_PRIOR_GAMES = 4
SOS_MAX_SWING = 0.25


def set_matchups(prof: Profile, pro_schedule: dict, ratings: dict, week: int, final_week: int) -> None:
    """Per-week multiplier on a player's value from his future opponents:
    1 + trust x (points that defense allows to his position / league average - 1),
    capped at +/-SOS_MAX_SWING. `trust` grows with games played, since two
    weeks of defensive stats are mostly noise."""
    opps = (pro_schedule.get(prof.team) or {}).get("opponents", {})
    pos = ratings.get(prof.position) or {}
    avg = pos.get("avg") or 0.0
    trust = (week - 1) / ((week - 1) + SOS_PRIOR_GAMES)
    prof.opponents = {w: o for w, o in opps.items() if week <= w <= final_week}
    prof.matchup = {}
    for w, opp in prof.opponents.items():
        allowed = (pos.get("by_opp") or {}).get(opp)
        if not avg or allowed is None:
            continue
        f = 1 + trust * (allowed / avg - 1)
        prof.matchup[w] = max(1 - SOS_MAX_SWING, min(1 + SOS_MAX_SWING, f))


def week_value(p: Profile, w: int, mode: str = "blend", haircut: dict | None = None) -> float:
    """Expected points in week w: per-game value x that week's matchup x an
    optional per-player haircut (used for pessimistic trade checks)."""
    v = max(p.ppg(mode) + p.team_boost.get(w, 0.0), 0.0) * p.matchup.get(w, 1.0)
    if haircut:
        v *= haircut.get(id(p), 1.0)
    return v


def profiles_for(players: list, history: dict, meta: dict, pro_schedule: dict,
                 week: int, final_week: int, ratings: dict | None = None,
                 team_ctx=None) -> list[Profile]:
    """Profiles with signals, availability and (with `ratings`) strength of
    schedule for a list of loaded players. `meta` maps player id ->
    get_player_meta() entry; players loaded through get_player_pool() carry
    their own `injury_details` as a fallback. `team_ctx` (a
    team_context.TeamContext) adds teammate-injury boosts to later weeks."""
    out = []
    for p in players:
        prof = build_profile(p, history.get(p.playerId), week)
        m = meta.get(p.playerId)
        details = m["injury"] if m else getattr(p, "injury_details", None)
        if m:
            prof.auction, prof.espn_rating = m["auction"], m["rating"]
        set_availability(prof, details, pro_schedule, week, final_week)
        if ratings:
            set_matchups(prof, pro_schedule, ratings, week, final_week)
        if team_ctx:
            team_ctx.apply(prof)
        out.append(prof)
    return out


def ros_market_value(p: Profile) -> float:
    """Context-free 'on paper' worth: per-game value x remaining games he can
    play. What a league-mate eyeballing a trade roughly sees, independent of
    either team's lineup needs."""
    return sum(max(p.ros_ppg + p.team_boost.get(w, 0.0), 0.0) for w in p.available)


def _ros_lineup_points(players: list[Profile], slots: list[str], w: int = 0, mode: str = "blend",
                       haircut: dict | None = None) -> tuple[float, list[Profile]]:
    def value(p):
        return week_value(p, w, mode, haircut)
    lineup = advisor.best_lineup(players, slots, key=value)
    starters = [p for p in lineup.values() if p]
    return sum(value(p) for p in starters), starters


def season_points(players: list[Profile], slots: list[str], week: int, final_week: int,
                  injury_risk: bool = True, mode: str = "blend",
                  haircut: dict | None = None) -> dict[int, float]:
    """Expected best-lineup points for each week from `week` to `final_week`,
    using only players available that week: ESPN's weekly projection for the
    current week (whose injury news is already known), and for later weeks
    the per-game value (see Profile.ppg `mode`) x that week's matchup.

    With `injury_risk`, each later week also allows for a starter getting
    hurt: for each starter, subtract (his WEEKLY_MISS_RATE) x (points lost if
    the best lineup had to do without him). That's a first-order expected
    value (it ignores two starters missing the same week), and it's what
    makes a backup at an injury-prone position worth rostering."""
    out = {}
    for w in range(week, final_week + 1):
        if w == week:
            out[w] = advisor.lineup_points(advisor.best_lineup(players, slots))
            continue
        avail = [p for p in players if w in p.available]
        points, starters = _ros_lineup_points(avail, slots, w, mode, haircut)
        if injury_risk:
            expected = points
            for s in starters:
                rate = WEEKLY_MISS_RATE.get(s.position, 0.0)
                if rate:
                    without, _ = _ros_lineup_points([p for p in avail if p is not s], slots, w, mode, haircut)
                    expected -= rate * (points - without)
            points = expected
        out[w] = points
    return out


def drop_ranking(roster: list[Profile], slot_counts: dict[str, int], week: int, final_week: int) -> list[dict]:
    """Roster players ordered from most to least droppable: by how many
    season points your best weekly lineups lose without them (this week
    through the playoffs, byes and injuries included), then by per-game value
    scaled by how many remaining weeks they can play (so an out-for-season
    player ranks as fully droppable). Your only player at a position you
    start is ranked as less droppable."""
    slots = advisor.starting_slots(slot_counts)
    n_weeks = max(final_week - week + 1, 1)
    full = sum(season_points(roster, slots, week, final_week).values())
    need = {s: n for s, n in slot_counts.items() if not advisor.is_flex(s) and s not in advisor.NON_STARTING_SLOTS}
    by_pos: dict[str, int] = {}
    for p in roster:
        by_pos[p.position] = by_pos.get(p.position, 0) + 1

    out = []
    for p in roster:
        loss = full - sum(season_points([q for q in roster if q is not p], slots, week, final_week).values())
        reasons = []
        only_depth = by_pos.get(p.position, 0) <= need.get(p.position, 0)
        if only_depth:
            reasons.append(f"your only {p.position}")
        if loss > 0.5:
            reasons.append(f"lineups lose {loss:.0f} pts without him")
        if p.out_for_season:
            reasons.append("out for season")
        elif p.return_date:
            reasons.append(f"{p.injury_type or 'injured'}, back ~{p.return_date:%b %d}")
        elif p.injury:
            reasons.append(p.injury.lower())
        if p.trend == "falling":
            reasons.append("usage falling")
        if p.team_note:
            reasons.append(p.team_note)
        avail_frac = len(p.available) / n_weeks
        score = (loss / n_weeks) * 10 + p.ros_ppg * avail_frac + (5 if only_depth else 0)
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
                     week: int, final_week: int, market: Market) -> list[dict]:
    """Every free agent whose addition (with the best drop for that specific
    add) raises your best weekly lineups' total from this week through the
    playoffs, with the weeks it helps and a bid."""
    slots = advisor.starting_slots(slot_counts)
    base = season_points(roster, slots, week, final_week)
    out = []
    for fa in pool:
        # Rank drops as if the new player were already on the roster, so
        # e.g. adding a TE frees up your old injured TE as a drop option.
        drops = [d for d in drop_ranking(roster + [fa], slot_counts, week, final_week) if d["player"] is not fa]
        drop = drops[0]
        after = season_points([p for p in roster if p is not drop["player"]] + [fa], slots, week, final_week)
        gains = {w: after[w] - base[w] for w in base}
        total = sum(gains.values())
        if total <= 0.05:
            continue
        stream = fa.position in STREAM_POSITIONS
        if stream and gains[week] <= 0.05:
            continue  # streams are judged on this week alone
        # Short-term: nearly all the value lands this week and next (a
        # stream, or a fill-in while a starter is hurt or on bye).
        later = sum(g for w, g in gains.items() if w > week + 1)
        short_term = not stream and later <= 0.25 * total
        fair, to_win = faab_bid(0 if stream else total, fa.pct_change, market, stream=stream or short_term)
        out.append({
            "add": fa, "drop": drop, "stream": stream, "short_term": short_term,
            "ros_gain_points": round(total, 1), "week_gain": round(gains[week], 1),
            "helps_weeks": [w for w, g in gains.items() if g > 0.05],
            "fair": fair, "to_win": to_win,
        })
    out.sort(key=lambda r: (r["stream"], r["short_term"],
                            -(r["week_gain"] if r["stream"] else r["ros_gain_points"])))
    return out


def league_winning_bids(league) -> list[int]:
    """Winning FAAB bids from the league's recent activity."""
    bids = []
    for act in league.recent_activity(size=100):
        for team, action, player, *rest in act.actions:
            if action == "WAIVER ADDED" and rest and isinstance(rest[0], (int, float)):
                bids.append(int(rest[0]))
    return bids
