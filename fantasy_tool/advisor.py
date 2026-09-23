"""Start/sit and waiver suggestions from weekly projections.

Platform-agnostic: works on RosterPlayer records (see espn_player() for the
ESPN adapter), so Yahoo can plug in later with its own adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Slots that never count toward the scoring lineup.
NON_STARTING_SLOTS = {"BE", "IR"}

# Injury statuses worth flagging on a starter (ESPN's values).
FLAG_STATUSES = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}


@dataclass
class RosterPlayer:
    name: str
    position: str
    team: str
    eligible_slots: list[str]
    proj: float  # this week's projected points
    season_avg: float  # projected points per game for the season
    injury: str | None = None
    slot: str = ""  # current lineup slot ("" for free agents)
    owned: float = 0.0
    raw: object = field(default=None, repr=False)


def espn_player(p, week: int) -> RosterPlayer:
    return RosterPlayer(
        name=p.name,
        position=p.position,
        team=p.proTeam,
        eligible_slots=list(p.eligibleSlots),
        proj=p.stats.get(week, {}).get("projected_points", 0.0),
        season_avg=p.projected_avg_points or 0.0,
        injury=p.injuryStatus if p.injuryStatus not in (None, "ACTIVE", "NORMAL") else None,
        slot=p.lineupSlot or "",
        owned=p.percent_owned,
        raw=p,
    )


def is_flex(slot: str) -> bool:
    """Flex slots combine positions ("RB/WR/TE", "OP"). "D/ST" has a slash
    but is a single position."""
    return slot == "OP" or ("/" in slot and slot != "D/ST")


def starting_slots(slot_counts: dict[str, int]) -> list[str]:
    """Expands {'RB': 2, 'RB/WR/TE': 1, ...} into a fill order: single-position
    slots first, flex slots last, so the flex takes the best player left over."""
    slots = [s for s, n in slot_counts.items() for _ in range(n) if n and s not in NON_STARTING_SLOTS]
    return sorted(slots, key=is_flex)


def best_lineup(players: list[RosterPlayer], slots: list[str], key=None) -> dict[int, RosterPlayer | None]:
    """Greedy highest-value fill, one player per slot. `slots` must be in
    starting_slots() order, which makes greedy optimal for the usual
    single-position-then-flex layout. `key` is the value to rank by
    (default: this week's projection)."""
    key = key or (lambda p: p.proj)
    # A player valued at 0 (injured, bye) adds nothing, so a slot with only
    # those options is left empty rather than "filled".
    pool = sorted((p for p in players if key(p) > 0), key=lambda p: -key(p))
    used: set[int] = set()
    lineup: dict[int, RosterPlayer | None] = {}
    for i, slot in enumerate(slots):
        pick = next((p for p in pool if id(p) not in used and slot in p.eligible_slots), None)
        if pick:
            used.add(id(pick))
        lineup[i] = pick
    return lineup


def lineup_points(lineup: dict[int, RosterPlayer | None]) -> float:
    return sum(p.proj for p in lineup.values() if p)


def check_lineup(roster: list[RosterPlayer], slot_counts: dict[str, int]) -> dict:
    """Compares the lineup as currently set against the best possible one."""
    slots = starting_slots(slot_counts)
    starters = [p for p in roster if p.slot and p.slot not in NON_STARTING_SLOTS]
    current_points = sum(p.proj for p in starters)

    # Empty slots: each slot count minus how many players are in it.
    empty = []
    for slot, n in slot_counts.items():
        if slot in NON_STARTING_SLOTS or not n:
            continue
        filled = sum(1 for p in starters if p.slot == slot)
        empty += [slot] * max(n - filled, 0)

    flagged = [p for p in starters if (p.injury in FLAG_STATUSES) or p.proj == 0]

    best = best_lineup(roster, slots)
    best_names = {p.name for p in best.values() if p}
    starter_names = {p.name for p in starters}
    return {
        "slots": slots,
        "current_points": current_points,
        "best_points": lineup_points(best),
        "best": best,
        "empty": empty,
        "flagged": flagged,
        "start": [p for p in roster if p.name in best_names - starter_names],
        "sit": [p for p in starters if p.name not in best_names],
    }


def waiver_suggestions(roster: list[RosterPlayer], free_agents: list[RosterPlayer],
                       slot_counts: dict[str, int], top: int = 5) -> list[dict]:
    """For each free agent: how much the best lineup would gain this week if
    you added them, and which bench player you'd drop. The drop is the lowest
    season-average player who isn't in the best lineup, so a good player on a
    bye week isn't suggested just because he projects 0 this week."""
    slots = starting_slots(slot_counts)
    base = best_lineup(roster, slots)
    base_points = lineup_points(base)
    in_lineup = {id(p) for p in base.values() if p}
    bench = [p for p in roster if id(p) not in in_lineup]
    if not bench:
        return []
    drop = min(bench, key=lambda p: (p.season_avg, p.proj))

    out = []
    for fa in free_agents:
        pts = lineup_points(best_lineup([p for p in roster if p is not drop] + [fa], slots))
        gain = round(pts - base_points, 1)
        if gain > 0:
            out.append({"add": fa, "drop": drop, "gain": gain, "new_points": pts})
    out.sort(key=lambda s: (-s["gain"], -s["add"].season_avg))
    return out[:top]
