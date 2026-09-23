"""This week's lineup check and waiver suggestions (read-only: it never
changes your roster or lineup).

Usage:
    python advise.py espn
"""
import sys

from fantasy_tool import advisor

platform = sys.argv[1].lower() if len(sys.argv) > 1 else "espn"
if platform != "espn":
    sys.exit("Only ESPN is supported so far (Yahoo access is still pending).")

from fantasy_tool import espn_client as espn  # noqa: E402

league = espn.get_league()
week = league.current_week
team = espn.get_my_team(league)
slot_counts = {k: v for k, v in league.settings.position_slot_counts.items() if v}

roster = [advisor.espn_player(p, week) for p in team.roster]
# Pull the top free agents at each position you actually start, so a
# position-specific need (e.g. an empty TE slot) isn't crowded out by QBs.
positions = {s for s in slot_counts if "/" not in s and s not in advisor.NON_STARTING_SLOTS}
free_agents, seen = [], set()
for pos in sorted(positions):
    for p in espn.get_free_agents(league, position=pos, size=15):
        if p.playerId not in seen:
            seen.add(p.playerId)
            free_agents.append(advisor.espn_player(p, week))


def label(p):
    inj = f" [{p.injury}]" if p.injury else ""
    return f"{p.name} ({p.position}, {p.team}, {p.proj:.1f}){inj}"


check = advisor.check_lineup(roster, slot_counts)
print(f"=== {team.team_name} — week {week} lineup check ===")
print(f"Lineup as set: {check['current_points']:.1f} projected   "
      f"Best possible from your roster: {check['best_points']:.1f}")

if check["empty"]:
    print(f"\n!! EMPTY starting slot(s): {', '.join(check['empty'])}")
if check["flagged"]:
    print("\n!! Starters who are injured or projected 0 (bye?):")
    for p in check["flagged"]:
        print(f"   - {label(p)}")
if check["start"] or check["sit"]:
    print("\nSuggested changes:")
    for p in check["start"]:
        print(f"   START {label(p)}")
    for p in check["sit"]:
        print(f"   SIT   {label(p)}")
elif not check["empty"]:
    print("\nYour lineup is already the best one by projection.")

print("\nBest lineup:")
for i, slot in enumerate(check["slots"]):
    p = check["best"][i]
    print(f"   {slot:9s} {label(p) if p else '(EMPTY - nobody on your roster projects above 0)'}")

print("\n=== Waiver pickups (vs. your best lineup this week) ===")
suggestions = advisor.waiver_suggestions(roster, free_agents, slot_counts)
if not suggestions:
    print("No free agent improves your best lineup this week.")
for s in suggestions:
    a, d = s["add"], s["drop"]
    print(f"   +{s['gain']:4.1f}  ADD {label(a)} owned {a.owned:.1f}%   "
          f"DROP {d.name} ({d.position}, season avg {d.season_avg:.1f})")
