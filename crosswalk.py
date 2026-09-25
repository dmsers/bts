"""
Beat the Streak — Player ID Crosswalk Builder v3 (Local Chadwick)
================================================================
Maps MLB Stats API player IDs → FanGraphs IDs using a local copy
of the Chadwick Bureau register (people.csv).

Avoids live FanGraphs API calls to prevent denials/rate-limits.
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta

import mysql.connector
import pandas as pd
import requests

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.crosswalk")

# Chadwick Register is split into 16 files (0-9, a-f)
CHADWICK_BASE_URL = "https://raw.githubusercontent.com/chadwickbureau/register/master/data/people-{}.csv"
HEX_CHARS = "0123456789abcdef"


def get_db():
    return mysql.connector.connect(**config.DB_CONFIG)


def update_register(force=False):
    """Download the latest Chadwick register if missing or old."""
    path = config.CHADWICK_REGISTER_PATH
    if path.exists() and not force:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if datetime.now() - mtime < timedelta(days=7):
            log.info(f"Using cached Chadwick register (updated {mtime.strftime('%Y-%m-%d')})")
            return

    log.info("Downloading split Chadwick register files...")
    all_dfs = []
    
    # We only need these columns
    cols = ["key_mlbam", "key_fangraphs", "name_first", "name_last"]

    for char in HEX_CHARS:
        url = CHADWICK_BASE_URL.format(char)
        try:
            log.info(f"  Fetching {url}...")
            # Use pandas directly to read the CSV from URL
            df = pd.read_csv(url, usecols=cols, low_memory=False)
            all_dfs.append(df)
        except Exception as e:
            log.error(f"  Failed to download {url}: {e}")
            continue

    if not all_dfs:
        log.error("Could not download any register files!")
        if not path.exists():
            raise Exception("No register data available")
        return

    combined_df = pd.concat(all_dfs, ignore_index=True)
    combined_df.to_csv(path, index=False)
    log.info(f"Saved combined register ({len(combined_df)} rows) to {path}")


def load_register() -> pd.DataFrame:
    """Load the combined Chadwick register CSV into a DataFrame."""
    update_register()
    log.info(f"Loading register from {config.CHADWICK_REGISTER_PATH}...")
    
    df = pd.read_csv(config.CHADWICK_REGISTER_PATH, low_memory=False)
    
    # Clean up IDs
    df["key_mlbam"] = pd.to_numeric(df["key_mlbam"], errors="coerce").fillna(0).astype(int)
    df["key_fangraphs"] = pd.to_numeric(df["key_fangraphs"], errors="coerce").fillna(0).astype(int)
    
    return df


def get_unmapped_ids(cursor, map_all=False):
    """Get MLB IDs from various tables that don't have a crosswalk entry yet."""
    # 1. Pitchers from games table
    cursor.execute("""
        SELECT DISTINCT away_pitcher_id as mlb_id, away_pitcher as name, 1 as is_p
        FROM games WHERE away_pitcher_id IS NOT NULL
        UNION
        SELECT DISTINCT home_pitcher_id as mlb_id, home_pitcher as name, 1 as is_p
        FROM games WHERE home_pitcher_id IS NOT NULL
        UNION
        # 2. Hitters from daily_lineups
        SELECT DISTINCT hitter_id as mlb_id, hitter_name as name, 0 as is_p
        FROM daily_lineups
    """)
    source_ids = {row[0]: (row[1], row[2]) for row in cursor.fetchall() if row[0]}
    
    if not map_all:
        # Check which are already in player_id_map
        cursor.execute("SELECT mlb_id FROM player_id_map")
        mapped = {row[0] for row in cursor.fetchall()}
        unmapped = {k: v for k, v in source_ids.items() if k not in mapped}
        return unmapped
    return source_ids


def save_mappings(cursor, mappings: list):
    """Save crosswalk mappings to database."""
    if not mappings:
        return
        
    sql = """
        INSERT INTO player_id_map
            (mlb_id, fg_id, player_name, mlb_name, fg_name,
             match_method, is_pitcher)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            fg_id        = VALUES(fg_id),
            fg_name      = VALUES(fg_name),
            match_method = VALUES(match_method)
    """
    cursor.executemany(sql, mappings)


def build_crosswalk(force_update=False, map_all=False):
    """Build/update the player ID crosswalk using local register."""
    if force_update:
        update_register(force=True)
        
    conn = get_db()
    cursor = conn.cursor()

    try:
        unmapped = get_unmapped_ids(cursor)
        if not unmapped and not map_all:
            log.info("No new unmapped IDs found.")
            return

        if map_all:
            log.info("Reprocessing all IDs from source tables...")
            # Re-fetch all, ignore 'mapped' check
            cursor.execute("""
                SELECT DISTINCT away_pitcher_id as mlb_id, away_pitcher as name, 1 as is_p
                FROM games WHERE away_pitcher_id IS NOT NULL
                UNION
                SELECT DISTINCT home_pitcher_id as mlb_id, home_pitcher as name, 1 as is_p
                FROM games WHERE home_pitcher_id IS NOT NULL
                UNION
                SELECT DISTINCT hitter_id as mlb_id, hitter_name as name, 0 as is_p
                FROM daily_lineups
            """)
            unmapped = {row[0]: (row[1], row[2]) for row in cursor.fetchall() if row[0]}

        log.info(f"Processing {len(unmapped)} IDs...")
        
        register_df = load_register()
        
        mappings = []
        not_found = []
        for mlb_id, (name, is_pitcher) in unmapped.items():
            # 1. Try look up by MLB ID (precise)
            match = register_df[register_df["key_mlbam"] == mlb_id]

            if not match.empty:
                row = match.iloc[0]
                fg_id = int(row["key_fangraphs"])
                chadwick_name = f"{row['name_first']} {row['name_last']}".strip()

                if fg_id > 0:
                    mappings.append((
                        mlb_id, fg_id, name, name, chadwick_name, 'chadwick_id', is_pitcher
                    ))
                else:
                    not_found.append((mlb_id, name, "No FG ID in register"))
            else:
                # 2. Try lookup by Name (fuzzy/fallback)
                first = name.split()[0] if name.split() else ""
                last = name.split()[-1] if name.split() else ""

                name_match = register_df[
                    (register_df["name_first"].str.lower() == first.lower()) &
                    (register_df["name_last"].str.lower() == last.lower())
                ]

                if not name_match.empty:
                    row = name_match.iloc[0]
                    fg_id = int(row["key_fangraphs"])
                    chadwick_name = f"{row['name_first']} {row['name_last']}".strip()
                    if fg_id > 0:
                        mappings.append((
                            mlb_id, fg_id, name, name, chadwick_name, 'chadwick_name', is_pitcher
                        ))
                    else:
                        not_found.append((mlb_id, name, "No FG ID in register (name match)"))
                else:
                    not_found.append((mlb_id, name, "MLB ID and Name not in register"))


        if mappings:
            save_mappings(cursor, mappings)
            conn.commit()
            log.info(f"Successfully mapped and saved {len(mappings)} players")
        
        if not_found:
            log.warning(f"Could not map {len(not_found)} players:")
            reasons = {}
            for mid, name, reason in not_found:
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in reasons.items():
                log.warning(f"  - {reason}: {count}")
            
            for mid, name, reason in not_found[:5]:
                log.info(f"    Example: {name} (MLB:{mid}) - {reason}")

    finally:
        cursor.close()
        conn.close()


def get_ids_from_stats(cursor):
    """Get IDs and names from hitter_stats and pitcher_stats tables."""
    cursor.execute("""
        SELECT DISTINCT hitter_id as fg_id, hitter_name as name, 0 as is_p
        FROM hitter_stats WHERE hitter_id > 0
        UNION
        SELECT DISTINCT pitcher_id as fg_id, pitcher_name as name, 1 as is_p
        FROM pitcher_stats WHERE pitcher_id > 0
    """)
    source_stats = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    
    # Filter out those already in player_id_map
    cursor.execute("SELECT fg_id FROM player_id_map WHERE fg_id > 0")
    mapped_fgs = {row[0] for row in cursor.fetchall()}
    
    unmapped = {k: v for k, v in source_stats.items() if k not in mapped_fgs}
    return unmapped


def build_crosswalk_from_stats():
    """Build crosswalk entries for players already in our stats tables."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        unmapped = get_ids_from_stats(cursor)
        if not unmapped:
            log.info("All stats players already in crosswalk.")
            return

        log.info(f"Mapping {len(unmapped)} players from stats tables...")
        register_df = load_register()
        
        mappings = []
        for fg_id, (name, is_pitcher) in unmapped.items():
            # Match by FG ID
            match = register_df[register_df["key_fangraphs"] == fg_id]
            if not match.empty:
                row = match.iloc[0]
                mlb_id = int(row["key_mlbam"])
                chad_name = f"{row['name_first']} {row['name_last']}".strip()
                if mlb_id > 0:
                    mappings.append((
                        mlb_id, fg_id, name, chad_name, chad_name, 'stats_fg_id', is_pitcher
                    ))
            else:
                # Match by Name
                first = name.split()[0] if name.split() else ""
                last = name.split()[-1] if name.split() else ""
                name_match = register_df[
                    (register_df["name_first"].str.lower() == first.lower()) &
                    (register_df["name_last"].str.lower() == last.lower())
                ]
                if not name_match.empty:
                    row = name_match.iloc[0]
                    mlb_id = int(row["key_mlbam"])
                    chad_name = f"{row['name_first']} {row['name_last']}".strip()
                    if mlb_id > 0:
                        mappings.append((
                            mlb_id, fg_id, name, chad_name, chad_name, 'stats_name', is_pitcher
                        ))

        if mappings:
            save_mappings(cursor, mappings)
            conn.commit()
            log.info(f"Saved {len(mappings)} mappings from stats tables")
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="BTS Player ID Crosswalk v3")
    parser.add_argument("--update", action="store_true",
                        help="Force update the local Chadwick register")
    parser.add_argument("--all", action="store_true",
                        help="Reprocess all players found in games/lineups")
    parser.add_argument("--stats", action="store_true",
                        help="Map players from hitter_stats/pitcher_stats tables")
    args = parser.parse_args()

    if args.stats:
        build_crosswalk_from_stats()
    else:
        build_crosswalk(force_update=args.update, map_all=args.all)


if __name__ == "__main__":
    main()
