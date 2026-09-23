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
team = espn.get_my_team(league)
slot_counts = {k: v for k, v in league.settings.position_slot_counts.items() if v}
positions = sorted(s for s in slot_counts if not advisor.is_flex(s) and s not in advisor.NON_STARTING_SLOTS)

# Candidates: the most-owned free agents at every position you start.
pool_raw = [p for pos in positions for p in espn.get_player_pool(league, pos, size=20)]
history = espn.get_history(league, [p.playerId for p in team.roster] + [p.playerId for p in pool_raw])
roster = [analysis.build_profile(p, history.get(p.playerId), week) for p in team.roster]
pool = [analysis.build_profile(p, history.get(p.playerId), week) for p in pool_raw]

# Games left through the fantasy playoffs, minus one for a bye (most teams'
# byes fall between weeks 5 and 14; not tracked per team yet).
final_week = getattr(league, "finalScoringPeriod", 17) or 17
games_left = max(final_week - week + 1 - (1 if week <= 14 else 0), 1)

budget = int(getattr(league.settings, "acquisition_budget", 0) or 0)
rivals = sorted((budget - t.acquisition_budget_spent for t in league.teams if t is not team), reverse=True)
market = analysis.Market(
    budget=budget,
    my_remaining=budget - team.acquisition_budget_spent,
    rival_remaining=rivals,
    winning_bids=analysis.league_winning_bids(league),
)


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
    if p.injury:
        bits.append(p.injury)
    return "; ".join(bits)


print(f"=== {team.team_name} — week {week} waiver analysis ===")
print(f"FAAB: ${market.my_remaining} of ${budget} left.  Rivals' remaining (top 3): "
      + ", ".join(f"${r}" for r in rivals[:3]))
paid = sorted(b for b in market.winning_bids if b > 0)
print(f"This league's recent winning bids: {len(market.winning_bids)} claims, "
      f"{len(market.winning_bids) - len(paid)} at $0; paid ones typical ${market.typical:g}, "
      f"high end ${market.high:g}" + (f", max ${paid[-1]}" if paid else "") + ".")
print(f"Rest-of-season horizon: ~{games_left} games (through week {final_week}).")
print("Opp rank: ESPN's rank of this week's opponent vs. the position, 1 = toughest, 32 = easiest.")

results = analysis.evaluate_pickups(roster, pool, slot_counts, games_left, market)
core = [r for r in results if not r["stream"] and not r["short_term"]]
short_term = [r for r in results if r["short_term"]]
streams = [r for r in results if r["stream"]]

print("\n--- Best pickups (ranked by rest-of-season points added to your best lineup) ---")
if not core:
    print("No free agent improves your best rest-of-season lineup right now.")
for i, r in enumerate(core[:8], 1):
    a, d = r["add"], r["drop"]["player"]
    print(f"\n{i}. {a.name} ({a.position}, {a.team})  +{r['ros_gain_points']:.0f} ROS pts "
          f"(+{r['ros_gain_ppg']:.1f}/g), +{r['week_gain']:.1f} this week")
    print(f"   {signals(a)}")
    print(f"   BID: ${r['fair']} fair, ${r['to_win']} to win     DROP: {d.name} ({d.position}, ROS {d.ros_ppg:.1f}/g"
          + (f"; {', '.join(r['drop']['reasons'])}" if r["drop"]["reasons"] else "") + ")")

if short_term:
    print("\n--- This-week fill-ins (help now, no rest-of-season gain; bid $0-1) ---")
    for r in short_term[:5]:
        a, d = r["add"], r["drop"]["player"]
        print(f"   +{r['week_gain']:.1f} this week  {a.name} ({a.position}, {a.team})   DROP {d.name}"
              + (f" ({', '.join(r['drop']['reasons'])})" if r["drop"]["reasons"] else ""))
        print(f"      {signals(a)}")

if streams:
    print("\n--- Streaming K / D/ST (this week only; bid $0-1) ---")
    for r in streams[:4]:
        a, d = r["add"], r["drop"]["player"]
        print(f"   +{r['week_gain']:.1f} this week  {a.name} ({a.position}) "
              f"vs {a.opponent} (opp rank {a.opp_rank}/32)   DROP {d.name}")

print("\n--- Your most droppable players ---")
for d in analysis.drop_ranking(roster, slot_counts)[:5]:
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
