"""Trade offers that help you more than the other team, but that they'd
still consider (read-only: never proposes anything on ESPN).

Usage:
    python trade_finder.py espn
    python trade_finder.py espn --team "Fuzz"          # one opponent (name or abbrev)
    python trade_finder.py espn --give "Kamara"        # offers built around a player of yours
    python trade_finder.py espn --get "Bijan"          # offers for a specific player
    python trade_finder.py espn --top 12
"""
import argparse

from fantasy_tool import advisor, analysis, trades

parser = argparse.ArgumentParser()
parser.add_argument("platform", nargs="?", default="espn")
parser.add_argument("--team", help="only this opponent (team name or abbreviation, partial match)")
parser.add_argument("--give", help="only offers that include this player of yours (partial name)")
parser.add_argument("--get", help="only offers for this player (partial name)")
parser.add_argument("--top", type=int, default=8)
args = parser.parse_args()
if args.platform != "espn":
    raise SystemExit("Only ESPN is supported so far (Yahoo access is still pending).")

from fantasy_tool import espn_client as espn  # noqa: E402

league = espn.get_league()
week = league.current_week
final_week = getattr(league, "finalScoringPeriod", 17) or 17
me_team = espn.get_my_team(league)
slot_counts = {k: v for k, v in league.settings.position_slot_counts.items() if v}
limit = sum(n for s, n in slot_counts.items() if s != "IR")

ids = [p.playerId for t in league.teams for p in t.roster]
history = espn.get_history(league, ids)
injuries = espn.get_injury_details(league, ids)
schedule = espn.get_pro_schedule(league)


def roster_of(t):
    return trades.TeamRoster(t, analysis.profiles_for(t.roster, history, injuries, schedule, week, final_week), limit)


me = roster_of(me_team)
others = [roster_of(t) for t in league.teams if t is not me_team]
if args.team:
    q = args.team.lower()
    others = [o for o in others if q in o.team.team_name.lower() or q == o.team.team_abbrev.lower()]
    if not others:
        raise SystemExit(f"No other team matches {args.team!r}.")


def name_filter(q):
    return (lambda p: q.lower() in p.name.lower()) if q else None


results = trades.find_trades(me, others, slot_counts, week, final_week,
                             give_filter=name_filter(args.give), get_filter=name_filter(args.get),
                             top=args.top)


def who(p):
    inj = f", {p.injury.lower()}" if p.injury else ""
    return f"{p.name} ({p.position}, {p.team}, {p.ros_ppg:.1f}/g{inj})"


def counts_str(players):
    c = trades.position_counts(players)
    return " ".join(f"{c.get(pos, 0)} {pos}" for pos in ("QB", "RB", "WR", "TE"))


print(f"=== Trade ideas for {me_team.team_name} (weeks {week}-{final_week}) ===")
print(f"Your roster: {counts_str(me.players)}")
print("Each offer: you gain more than they do, their lineups still improve, "
      f"and on paper they get at least {trades.PAPER_FAIRNESS:.0%} of what they give.")
if not results:
    print("\nNo offer passes all the checks with these filters. Try --team or --get to widen the search, "
          "or loosen trades.PAPER_FAIRNESS.")
for i, r in enumerate(results, 1):
    o = r["other"]
    print(f"\n{i}. With {o.team.team_name} ({o.team.team_abbrev}, {o.team.wins}-{o.team.losses}; "
          f"they have {counts_str(o.players)})")
    print(f"   YOU GIVE: {', '.join(who(p) for p in r['give'])}")
    print(f"   YOU GET:  {', '.join(who(p) for p in r['get'])}")
    print(f"   Season points: you {r['my_gain']:+.1f}, them {r['their_gain']:+.1f}.   "
          f"On paper (ROS pts): you give {r['paper_give']:.0f}, get {r['paper_get']:.0f}.")
    if r["my_open_spots"]:
        print(f"   Opens {r['my_open_spots']} roster spot(s) for you to fill from waivers.")
    if r["cut"]:
        print(f"   They'd need to cut: {', '.join(p.name for p in r['cut'])}")

print("\nThese are ideas to open a conversation, not guarantees. People weigh names, "
      "loyalty and their own projections. Nothing here is sent on ESPN.")
