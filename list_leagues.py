"""One-off helper: prints your NFL league keys so you can fill YAHOO_LEAGUE_KEY in .env."""
import yahoo_fantasy_api as yfa

from fantasy_tool.yahoo_client import get_session

oauth = get_session()
game = yfa.Game(oauth, "nfl")
for league_id in game.league_ids():
    print(league_id)
