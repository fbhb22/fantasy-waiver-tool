from fantasy_tool.yahoo_client import get_league, get_my_roster, get_free_agents

league = get_league()

print("=== My roster ===")
for player in get_my_roster(league):
    print(f"{player['name']:25s} {player['position_type']:5s} {player['selected_position']}")

print("\n=== Available free agents (top 15, all positions) ===")
for player in get_free_agents(league)[:15]:
    print(f"{player['name']:25s} {player['eligible_positions']}")
