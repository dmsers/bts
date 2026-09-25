"""
BTS - Backfill Results
=======================
Fetches boxscore data for a range of dates and populates the results table,
then updates hitter_stats with actual games_with_hit and hit_rate.
"""
import argparse
import logging
import sys
from datetime import date, timedelta, datetime
import mysql.connector
import statsapi
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.backfill")

def get_db():
    return mysql.connector.connect(**config.DB_CONFIG)

def fetch_results_for_date(target_date: date):
    date_str = target_date.strftime("%Y-%m-%d")
    games = statsapi.schedule(date=date_str)
    
    results = []
    for game in games:
        game_pk = game["game_id"]
        status = game.get("status", "")
        if "Final" not in status and "Game Over" not in status:
            continue

        try:
            box = statsapi.boxscore_data(game_pk)
            for side in ["away", "home"]:
                players = box.get(side, {}).get("players", {})
                for p_key, p_data in players.items():
                    p_id = p_data["person"]["id"]
                    name = p_data["person"]["fullName"]
                    batting = p_data.get("stats", {}).get("batting", {})
                    ab = batting.get("atBats", 0)
                    hits = batting.get("hits", 0)
                    if ab > 0 or hits > 0:
                        results.append({
                            "game_date": target_date,
                            "game_pk": game_pk,
                            "hitter_id": p_id,
                            "hitter_name": name,
                            "ab": ab,
                            "hits": hits,
                            "got_hit": 1 if hits > 0 else 0
                        })
        except Exception as e:
            log.error(f"  Error fetching boxscore for {game_pk}: {e}")
    return results

def save_results(cursor, results):
    for r in results:
        cursor.execute("""
            INSERT INTO results (game_date, game_pk, hitter_id, hitter_name, ab, hits, got_hit)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                ab = VALUES(ab),
                hits = VALUES(hits),
                got_hit = VALUES(got_hit)
        """, (r["game_date"], r["game_pk"], r["hitter_id"], r["hitter_name"], r["ab"], r["hits"], r["got_hit"]))

def update_hitter_stats(cursor, season):
    log.info(f"Updating hitter_stats for season {season} from results table...")
    
    # 1. Get all players from player_id_map to link MLB ID -> FG ID
    cursor.execute("SELECT mlb_id, fg_id FROM player_id_map WHERE fg_id IS NOT NULL")
    id_map = {row[0]: row[1] for row in cursor.fetchall()}
    
    # 2. Aggregate results by player
    cursor.execute("""
        SELECT hitter_id, COUNT(*) as gp, SUM(got_hit) as gwh
        FROM results
        WHERE game_date >= %s AND game_date <= %s
        GROUP BY hitter_id
    """, (f"{season}-01-01", f"{season}-12-31"))
    
    player_results = cursor.fetchall()
    updated = 0
    for mlb_id, gp, gwh in player_results:
        fg_id = id_map.get(mlb_id)
        if not fg_id:
            continue
            
        hit_rate = gwh / gp if gp > 0 else 0
        cursor.execute("""
            UPDATE hitter_stats
            SET games_with_hit = %s, games_played = %s, hit_rate = %s
            WHERE hitter_id = %s AND season = %s
        """, (gwh, gp, hit_rate, fg_id, season))
        updated += 1
    
    log.info(f"Updated stats for {updated} hitters.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    
    conn = get_db()
    cursor = conn.cursor()
    
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=args.days)
    
    current_date = start_date
    while current_date <= end_date:
        log.info(f"Fetching results for {current_date}")
        results = fetch_results_for_date(current_date)
        if results:
            save_results(cursor, results)
            conn.commit()
            log.info(f"  Saved {len(results)} hitter results")
        current_date += timedelta(days=1)
        
    update_hitter_stats(cursor, config.CURRENT_SEASON)
    conn.commit()
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
