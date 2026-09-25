"""
BTS - Baseline Monitor
=======================
Checks the actual league-wide averages (BA and BAA) in the database 
and compares them to the model's constants in config.py.
"""
import logging
import sys
from datetime import date, timedelta
import mysql.connector
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.monitor")

def get_db():
    return mysql.connector.connect(**config.DB_CONFIG)

def check_baselines():
    conn = get_db()
    cursor = conn.cursor()
    
    season = config.CURRENT_SEASON
    
    # 1. League-wide BA (Season to date)
    # Weighted by AB to get true league average
    cursor.execute("""
        SELECT SUM(hits) / SUM(ab) 
        FROM hitter_stats 
        WHERE season = %s AND ab > 0
    """, (season,))
    actual_ba = float(cursor.fetchone()[0] or 0)
    
    # 2. League-wide BAA (Season to date)
    # Weighted by "Batters Faced" (approx via IP and stats)
    # Since we don't have BF directly in all records, we'll use a simple average of qualified arms
    cursor.execute("""
        SELECT AVG(ba_against) 
        FROM pitcher_stats 
        WHERE season = %s AND ip >= 20
    """, (season,))
    actual_baa = float(cursor.fetchone()[0] or 0)
    
    # 3. Recent Trend (Last 7 days from results table)
    cursor.execute("""
        SELECT SUM(hits) / SUM(ab) 
        FROM results 
        WHERE game_date >= %s
    """, (date.today() - timedelta(days=7),))
    recent_ba = float(cursor.fetchone()[0] or 0)

    print(f"\n{'='*60}")
    print(f"  BTS BASELINE MONITOR — SEASON {season}")
    print(f"{'='*60}")
    
    print(f"\n  HITTER BA (League Wide):")
    print(f"    Current Config: {config.LEAGUE_BA_BASELINE:.3f}")
    print(f"    Season Actual:  {actual_ba:.3f}")
    print(f"    Last 7 Days:    {recent_ba:.3f}")
    
    diff_ba = actual_ba - config.LEAGUE_BA_BASELINE
    if abs(diff_ba) > 0.005:
        print(f"    ⚠️  ALERT: League BA is {diff_ba:+.3f} vs baseline. Update recommended.")
    else:
        print(f"    ✅ Baseline is within tolerance (+/- .005).")

    print(f"\n  PITCHER BAA (League Wide):")
    print(f"    Current Config: {config.LEAGUE_BAA_BASELINE:.3f}")
    print(f"    Season Actual:  {actual_baa:.3f}")
    
    diff_baa = actual_baa - config.LEAGUE_BAA_BASELINE
    if abs(diff_baa) > 0.005:
        print(f"    ⚠️  ALERT: League BAA is {diff_baa:+.3f} vs baseline. Update recommended.")
    else:
        print(f"    ✅ Baseline is within tolerance (+/- .005).")

    print(f"\n{'='*60}\n")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    check_baselines()
