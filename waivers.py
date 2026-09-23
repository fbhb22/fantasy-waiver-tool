"""Pickup/drop analysis with FAAB bid suggestions (read-only: never changes
your roster, never places a bid).

Usage:
    python waivers.py espn
"""
import sys

from fantasy_tool import advisor, analysis

platform = sys.argv[1].lower() if len(sys.argv) > 1 else "espn"
if platform != "espn":
    sys.exit("Only ESPN is supported so far (Yahoo access is still pending).")

from fantasy_tool import espn_client as espn  # noqa: E402

league = espn.get_league()
week = league.current_week
final_week = getattr(league, "finalScoringPeriod", 17) or 17
team = espn.get_my_team(league)
slot_counts = {k: v for k, v in league.settings.position_slot_counts.items() if v}
slots = advisor.starting_slots(slot_counts)
positions = sorted(s for s in slot_counts if not advisor.is_flex(s) and s not in advisor.NON_STARTING_SLOTS)

# Candidates: the most-owned free agents at every position you start.
pool_raw = [p for pos in positions for p in espn.get_player_pool(league, pos, size=20)]
history = espn.get_history(league, [p.playerId for p in team.roster] + [p.playerId for p in pool_raw])
pro_schedule = espn.get_pro_schedule(league)
roster_meta = espn.get_player_meta(league, [p.playerId for p in team.roster])
ratings = espn.get_matchup_ratings(league)

roster = analysis.profiles_for(team.roster, history, roster_meta, pro_schedule, week, final_week, ratings)
pool = analysis.profiles_for(pool_raw, history, {}, pro_schedule, week, final_week, ratings)

budget = int(getattr(league.settings, "acquisition_budget", 0) or 0)
rivals = sorted((budget - t.acquisition_budget_spent for t in league.teams if t is not team), reverse=True)
market = analysis.Market(
    budget=budget,
    my_remaining=budget - team.acquisition_budget_spent,
    rival_remaining=rivals,
    winning_bids=analysis.league_winning_bids(league),
)


def weeks_str(weeks: list[int]) -> str:
    """[3, 4, 5, 8] -> '3-5, 8'"""
    out, run = [], []
    for w in sorted(weeks):
        if run and w != run[-1] + 1:
            out.append(run)
            run = []
        run.append(w)
    if run:
        out.append(run)
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in out)


def injury_str(p: analysis.Profile) -> str:
    if not (p.injury or p.injury_type or p.out_for_season):
        return ""
    bits = [p.injury or "injured"]
    if p.injury_type:
        bits.append(p.injury_type)
    if p.out_for_season:
        bits.append("out for season")
    elif p.return_date:
        bits.append(f"back ~{p.return_date:%b %d}")
    if p.weeks_missed:
        bits.append(f"misses wk {weeks_str(p.weeks_missed)}")
    return " / ".join(bits)


def signals(p: analysis.Profile) -> str:
    bits = [f"ROS {p.ros_ppg:.1f}/g (ESPN {p.season_avg:.1f}"
            + (f", actual {p.actual_ppg:.1f} over {p.games}g" if p.games else "") + ")"]
    if p.opps_per_game is not None:
        bits.append(f"opps {p.opps_per_game:.1f}/g, last {p.last_opps:.0f}" + (f" {p.trend.upper()}" if p.trend else ""))
    if abs(p.pct_change) >= 0.5:
        bits.append(f"owned {p.owned:.0f}% ({p.pct_change:+.1f} this week)")
    else:
        bits.append(f"owned {p.owned:.0f}%")
    if p.opponent and p.opponent != "None":
        bits.append(f"wk{week} vs {p.opponent} (opp rank {p.opp_rank}/32)")
    if p.bye_week and p.bye_week >= week:
        bits.append(f"bye wk {p.bye_week}")
    inj = injury_str(p)
    if inj:
        bits.append(inj)
    return "; ".join(bits)


def news_line(p: analysis.Profile, indent: str = "      ") -> None:
    for date, headline in espn.get_news(p.player_id, p.name, limit=1):
        print(f"{indent}News {date}: {headline[:160]}")


print(f"=== {team.team_name} — week {week} waiver analysis ===")
print(f"FAAB: ${market.my_remaining} of ${budget} left.  Rivals' remaining (top 3): "
      + ", ".join(f"${r}" for r in rivals[:3]))
paid = sorted(b for b in market.winning_bids if b > 0)
print(f"This league's recent winning bids: {len(market.winning_bids)} claims, "
      f"{len(market.winning_bids) - len(paid)} at $0; paid ones typical ${market.typical:g}, "
      f"high end ${market.high:g}" + (f", max ${paid[-1]}" if paid else "") + ".")
print(f"Season horizon: weeks {week}-{final_week}, with real bye weeks and ESPN's expected injury return dates.")
print("Opp rank: ESPN's rank of this week's opponent vs. the position, 1 = toughest, 32 = easiest.")

# --- Your injuries ---
hurt = [p for p in roster if p.injury or p.injury_type or p.weeks_missed]
if hurt:
    print("\n--- Your injured players ---")
    for p in hurt:
        print(f"   {p.name} ({p.position}, {p.team}): {injury_str(p)}")
        news_line(p)

# --- Bye-week / injury holes in your best lineup, week by week ---
holes = []
for w in range(week + 1, final_week + 1):
    avail = [p for p in roster if w in p.available]
    lineup = advisor.best_lineup(avail, slots, key=lambda p: p.ros_ppg)
    empty = [slots[i] for i, p in lineup.items() if p is None]
    if empty:
        out = sorted(p.name for p in roster
                     if w not in p.available and any(s in p.eligible_slots for s in empty))
        holes.append((w, empty, out))
if holes:
    print("\n--- Weeks your roster can't fill a starting slot ---")
    for w, empty, out in holes:
        print(f"   Week {w}: no one for {', '.join(empty)}" + (f" (out: {', '.join(out)})" if out else ""))

results = analysis.evaluate_pickups(roster, pool, slot_counts, week, final_week, market)
core = [r for r in results if not r["stream"] and not r["short_term"]]
short_term = [r for r in results if r["short_term"]]
streams = [r for r in results if r["stream"]]


def drop_str(r) -> str:
    d = r["drop"]["player"]
    return f"{d.name} ({d.position}, ROS {d.ros_ppg:.1f}/g" + (
        f"; {', '.join(r['drop']['reasons'])}" if r["drop"]["reasons"] else "") + ")"


print("\n--- Best pickups (season points added to your best weekly lineups) ---")
if not core:
    print("No free agent adds lasting value to your lineup right now.")
for i, r in enumerate(core[:6], 1):
    a = r["add"]
    print(f"\n{i}. {a.name} ({a.position}, {a.team})  +{r['ros_gain_points']:.0f} pts over the season "
          f"(+{r['week_gain']:.1f} this week; helps wk {weeks_str(r['helps_weeks'])})")
    print(f"   {signals(a)}")
    news_line(a, indent="   ")
    print(f"   BID: ${r['fair']} fair, ${r['to_win']} to win     DROP: {drop_str(r)}")

if short_term:
    print("\n--- Short-term fill-ins (value is this week/next; bid $0-1) ---")
    for r in short_term[:5]:
        a = r["add"]
        print(f"   +{r['ros_gain_points']:.1f} pts (wk {weeks_str(r['helps_weeks'])})  {a.name} ({a.position}, {a.team})"
              f"   DROP {drop_str(r)}")
        print(f"      {signals(a)}")

if streams:
    print("\n--- Streaming K / D/ST (bid $0-1) ---")
    for r in streams[:4]:
        a, d = r["add"], r["drop"]["player"]
        print(f"   +{r['week_gain']:.1f} this week  {a.name} ({a.position}) "
              f"vs {a.opponent} (opp rank {a.opp_rank}/32)   DROP {d.name}")

print("\n--- Your most droppable players ---")
for d in analysis.drop_ranking(roster, slot_counts, week, final_week)[:5]:
    p = d["player"]
    print(f"   {p.name:22s} {p.position:4s} ROS {p.ros_ppg:4.1f}/g"
          + (f"  ({', '.join(d['reasons'])})" if d["reasons"] else ""))

hot = sorted((p for p in pool if p.pct_change >= 5), key=lambda p: -p.pct_change)[:5]
if hot:
    print("\n--- Being added fast across ESPN (competition for these) ---")
    for p in hot:
        print(f"   {p.name:22s} {p.position:4s} {p.pct_change:+.1f}% owned this week (now {p.owned:.0f}%)")

print("\nBids are a heuristic from your league's own prices, not a guarantee. "
      "Nothing here changes your team; make claims in the ESPN app.")
