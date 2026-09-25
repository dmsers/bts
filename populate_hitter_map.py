"""
Populate player_id_map with hitter MLB→FG ID mappings
by matching daily_lineups (MLB IDs) against hitter_stats (FG IDs) by name.
"""
import mysql.connector
import config

def populate():
    conn = mysql.connector.connect(**config.DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    try:
        # Find matches by name between lineups (MLB ID) and stats (FG ID)
        cursor.execute("""
            SELECT dl.hitter_id AS mlb_id, hs.hitter_id AS fg_id, dl.hitter_name
            FROM daily_lineups dl
            JOIN hitter_stats hs 
              ON LOWER(TRIM(dl.hitter_name)) = LOWER(TRIM(hs.hitter_name))
              AND hs.season = 2025
            WHERE dl.hitter_id != hs.hitter_id
            GROUP BY dl.hitter_id, hs.hitter_id
        """)
        matches = cursor.fetchall()
        print(f"Found {len(matches)} name matches")

        inserted = 0
        skipped = 0
        for m in matches:
            # Check if this MLB ID already exists
            cursor.execute("""
                SELECT mlb_id FROM player_id_map WHERE mlb_id = %s
            """, (m['mlb_id'],))
            if cursor.fetchone():
                skipped += 1
                continue

            # Insert the mapping
            cursor.execute("""
                INSERT INTO player_id_map (mlb_id, fg_id, player_name, mlb_name, fg_name, match_method, is_pitcher)
                VALUES (%s, %s, %s, %s, %s, 'exact', 0)
                ON DUPLICATE KEY UPDATE fg_id = VALUES(fg_id)
            """, (m['mlb_id'], m['fg_id'], m['hitter_name'], m['hitter_name'], m['hitter_name']))
            inserted += 1

        conn.commit()
        print(f"Inserted: {inserted}, Skipped (already existed): {skipped}")

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    populate()