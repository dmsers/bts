import mysql.connector
from datetime import date
import config

def view_past_picks(target_date):
    try:
        conn = mysql.connector.connect(**config.DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # Using backticks for the reserved word 'rank'
        query = """
            SELECT `rank`, hitter_name, team_abbr, opp_pitcher_name, p_at_least_1, has_hit 
            FROM recommendations 
            WHERE game_date = %s 
            ORDER BY `rank` ASC
        """
        cursor.execute(query, (target_date,))
        rows = cursor.fetchall()

        if not rows:
            print(f"\n No records found in the database for {target_date}.")
            return

        print(f"\n--- BTS Performance History: {target_date} ---")
        print(f"{'Rk':<3} | {'Hitter':<20} | {'vs Pitcher':<18} | {'P(Hit)':<7} | {'Result'}")
        print("-" * 70)

        for r in rows:
            # Handle the TINYINT(1) mapping for the result
            res = "---"
            if r['has_hit'] == 1: res = "✅ HIT"
            elif r['has_hit'] == 0: res = "❌ 0-fer"
            
            print(f"{r['rank']:<3} | {r['hitter_name'][:20]:<20} | {r['opp_pitcher_name'][:18]:<18} | {r.get('p_at_least_1', 0):.1%} | {res}")

    except Exception as e:
        print(f"Connection error: {e}")
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

if __name__ == '__main__':
    # You can change this to any date you've saved in the DB
    view_past_picks(date(2026, 3, 28))