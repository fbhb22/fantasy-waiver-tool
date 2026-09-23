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
parser.add_argument("--add", action="append", default=[],
                    help="assume you've added this free agent (pending claim); repeatable")
parser.add_argument("--drop", action="append", default=[],
                    help="assume you've dropped this player of yours; repeatable")
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
meta = espn.get_player_meta(league, ids)
schedule = espn.get_pro_schedule(league)
ratings = espn.get_matchup_ratings(league)


def roster_of(t):
    return trades.TeamRoster(
        t, analysis.profiles_for(t.roster, history, meta, schedule, week, final_week, ratings), limit)


me = roster_of(me_team)

# Pending moves: model the roster as it will be once claims process.
for q in args.drop:
    hit = [p for p in me.players if q.lower() in p.name.lower()]
    if len(hit) != 1:
        raise SystemExit(f"--drop {q!r} matches {len(hit)} of your players; be more specific.")
    me.players.remove(hit[0])
for q in args.add:
    found = league.player_info(name=q)
    if not found:
        raise SystemExit(f"--add {q!r}: no player found with that name.")
    found = found if not isinstance(found, list) else found[0]
    extra_meta = espn.get_player_meta(league, [found.playerId])
    extra_hist = espn.get_history(league, [found.playerId])
    me.players += analysis.profiles_for([found], extra_hist, extra_meta, schedule, week, final_week, ratings)
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
    inj = ""
    if p.return_date or p.out_for_season:
        inj = ", out for season" if p.out_for_season else f", out until ~{p.return_date:%b %d}"
    elif p.injury:
        inj = f", {p.injury.lower()}"
    return f"{p.name} ({p.position}, {p.team}, {p.ros_ppg:.1f}/g{inj})"


def counts_str(players):
    c = trades.position_counts(players)
    return " ".join(f"{c.get(pos, 0)} {pos}" for pos in ("QB", "RB", "WR", "TE"))


print(f"=== Trade ideas for {me_team.team_name} (weeks {week}-{final_week}) ===")
print(f"Your roster: {counts_str(me.players)}"
      + (f"  (assuming: +{', +'.join(args.add)}" if args.add else "")
      + (f"{'; ' if args.add else '  (assuming: '}-{', -'.join(args.drop)})" if args.drop else (")" if args.add else "")))
print("Each offer: you gain under every scoring view (blend / ESPN-only / actual-heavy), you gain more than")
print(f"they do, their lineups still improve, and by ESPN market value they get >= {trades.PERCEIVED_FAIRNESS:.0%} of what they give.")
print("Confidence HIGH = you still gain if the players you get come in 10% under projection.")
if not results:
    print("\nNo offer passes all the checks with these filters. Try --team or --get to widen the search, "
          "or loosen trades.PERCEIVED_FAIRNESS.")
for i, r in enumerate(results, 1):
    o = r["other"]
    print(f"\n{i}. With {o.team.team_name} ({o.team.team_abbrev}, {o.team.wins}-{o.team.losses}; "
          f"they have {counts_str(o.players)})")
    print(f"   YOU GIVE: {', '.join(who(p) for p in r['give'])}")
    print(f"   YOU GET:  {', '.join(who(p) for p in r['get'])}")
    lo, hi = r["gain_range"]
    print(f"   You: {r['my_gain']:+.1f} season pts (range {lo:+.1f} to {hi:+.1f} across views), "
          f"confidence {r['confidence'].upper()}.   Them: {r['their_gain']:+.1f}.")
    print(f"   Market value: they get {r['perceived_ratio']:.0%} of what they give "
          f"({r['pv_give']:.0f} vs {r['pv_get']:.0f})  ->  acceptance {r['acceptance'].upper()}")
    if r["their_upgrades"]:
        print(f"   Why they'd bite: {', '.join(p.name + ' (' + p.position + ')' for p in r['their_upgrades'])} "
              f"would start for them")
    if r["my_open_spots"]:
        print(f"   Opens {r['my_open_spots']} roster spot(s) for you to fill from waivers.")
    if r["cut"]:
        print(f"   They'd need to cut: {', '.join(p.name for p in r['cut'])}")

print("\nThese are ideas to open a conversation, not guarantees. People weigh names, "
      "loyalty and their own projections. Nothing here is sent on ESPN.")
