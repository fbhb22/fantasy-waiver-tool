"""Show your current roster and the top available free agents.

Usage:
    python main.py espn
    python main.py yahoo   (default)
"""
import sys

platform = sys.argv[1].lower() if len(sys.argv) > 1 else "yahoo"

if platform == "espn":
    from fantasy_tool import espn_client as espn

    league = espn.get_league()
    week = league.current_week
    team = espn.get_my_team(league)

    print(f"=== {team.team_name} ({team.wins}-{team.losses}) — week {week} ===")
    for p in sorted(team.roster, key=lambda p: -espn.week_projection(p, week)):
        injury = f" [{p.injuryStatus}]" if p.injuryStatus not in (None, "ACTIVE", "NORMAL") else ""
        print(f"{p.name:25s} {p.position:4s} {p.proTeam:4s} slot={p.lineupSlot:5s} "
              f"proj={espn.week_projection(p, week):5.1f}{injury}")

    print("\n=== Available free agents (top 15 by this week's projection) ===")
    fas = sorted(espn.get_free_agents(league), key=lambda p: -espn.week_projection(p, week))
    for p in fas[:15]:
        print(f"{p.name:25s} {p.position:4s} {p.proTeam:4s} proj={espn.week_projection(p, week):5.1f} "
              f"owned={p.percent_owned:5.1f}%")

elif platform == "yahoo":
    from fantasy_tool.yahoo_client import get_league, get_my_roster, get_free_agents

    league = get_league()

    print("=== My roster ===")
    for player in get_my_roster(league):
        print(f"{player['name']:25s} {player['position_type']:5s} {player['selected_position']}")

    print("\n=== Available free agents (top 15, all positions) ===")
    for player in get_free_agents(league)[:15]:
        print(f"{player['name']:25s} {player['eligible_positions']}")

else:
    sys.exit(f"Unknown platform {platform!r} — use 'espn' or 'yahoo'.")
