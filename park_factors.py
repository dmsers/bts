"""
Beat the Streak — Park Factors Loader
======================================
Populates the park_factors table with MLB park data.
Hit factor > 1.0 = hitter friendly, < 1.0 = pitcher friendly.

Source: FanGraphs 2023-2025 park factors (3-year average).

Usage:
    python park_factors.py
"""

import logging
import sys

import mysql.connector

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.park_factors")


# 3-year average park factors (FanGraphs, 2023-2025)
# hit_factor: multiplier for hits (1.00 = neutral)
# hr_factor: multiplier for home runs
# run_factor: multiplier for runs
PARK_DATA = [
    # (park_id, team_abbr, park_name, hr_factor, hit_factor, run_factor)
    ("coors_field",       "COL",  "Coors Field",              1.30, 1.15, 1.27),
    ("great_american",    "CIN",  "Great American Ball Park",  1.18, 1.04, 1.10),
    ("fenway_park",       "BOS",  "Fenway Park",              1.10, 1.04, 1.08),
    ("citizens_bank",     "PHI",  "Citizens Bank Park",       1.12, 1.03, 1.07),
    ("wrigley_field",     "CHC",  "Wrigley Field",            1.10, 1.02, 1.06),
    ("globe_life",        "TEX",  "Globe Life Field",         1.08, 1.02, 1.05),
    ("yankee_stadium",    "NYY",  "Yankee Stadium",           1.15, 1.01, 1.05),
    ("citi_field",        "NYM",  "Citi Field",               0.95, 1.01, 1.02),
    ("camden_yards",      "BAL",  "Camden Yards",             1.05, 1.01, 1.03),
    ("minute_maid",       "HOU",  "Minute Maid Park",         1.02, 1.01, 1.02),
    ("target_field",      "MIN",  "Target Field",             1.00, 1.00, 1.01),
    ("busch_stadium",     "STL",  "Busch Stadium",            0.95, 1.00, 0.99),
    ("american_family",   "MIL",  "American Family Field",    1.02, 1.00, 1.01),
    ("angel_stadium",     "LAA",  "Angel Stadium",            0.95, 1.00, 0.98),
    ("rogers_centre",     "TOR",  "Rogers Centre",            1.05, 1.00, 1.02),
    ("guaranteed_rate",   "CWS",  "Guaranteed Rate Field",    1.05, 1.00, 1.02),
    ("nationals_park",    "WSH",  "Nationals Park",           1.00, 0.99, 1.00),
    ("pnc_park",          "PIT",  "PNC Park",                 0.90, 0.99, 0.97),
    ("chase_field",       "AZ",   "Chase Field",              1.05, 0.99, 1.01),
    ("dodger_stadium",    "LAD",  "Dodger Stadium",           0.92, 0.99, 0.97),
    ("sutter_health",     "OAK",  "Sutter Health Park",       1.08, 0.99, 1.03),
    ("progressive_field", "CLE",  "Progressive Field",        0.95, 0.99, 0.98),
    ("kauffman_stadium",  "KC",   "Kauffman Stadium",         0.90, 0.98, 0.96),
    ("tropicana",         "TB",   "Tropicana Field",          0.88, 0.98, 0.95),
    ("truist_park",       "ATL",  "Truist Park",              1.00, 0.98, 0.99),
    ("comerica_park",     "DET",  "Comerica Park",            0.88, 0.98, 0.95),
    ("loanDepot_park",    "MIA",  "loanDepot Park",           0.85, 0.97, 0.93),
    ("petco_park",        "SD",   "Petco Park",               0.88, 0.97, 0.94),
    ("t_mobile_park",     "SEA",  "T-Mobile Park",            0.85, 0.96, 0.93),
    ("oracle_park",       "SF",   "Oracle Park",              0.82, 0.96, 0.92),
]

# Map team abbreviations used by MLB API to our park IDs
# The API uses various formats, so we map common variants
TEAM_ABBR_MAP = {
    "COL": "coors_field", "Color": "coors_field",
    "CIN": "great_american", "Cinci": "great_american",
    "BOS": "fenway_park", "Bosto": "fenway_park",
    "PHI": "citizens_bank", "Phila": "citizens_bank",
    "CHC": "wrigley_field", "Chica": "wrigley_field",  # Cubs
    "TEX": "globe_life", "Texas": "globe_life",
    "NYY": "yankee_stadium", "New Y": "yankee_stadium",  # Yankees
    "NYM": "citi_field",  # Mets — handled by home_team logic
    "BAL": "camden_yards", "Balti": "camden_yards",
    "HOU": "minute_maid", "Houst": "minute_maid",
    "MIN": "target_field", "Minne": "target_field",
    "STL": "busch_stadium", "St. L": "busch_stadium",
    "MIL": "american_family", "Milwa": "american_family",
    "LAA": "angel_stadium", "Los A": "angel_stadium",  # Angels
    "TOR": "rogers_centre", "Toron": "rogers_centre",
    "CWS": "guaranteed_rate",  # White Sox
    "WSH": "nationals_park", "Washi": "nationals_park",
    "PIT": "pnc_park", "Pitts": "pnc_park",
    "AZ": "chase_field", "Arizo": "chase_field",
    "LAD": "dodger_stadium",  # Dodgers
    "OAK": "sutter_health", "Athle": "sutter_health",
    "CLE": "progressive_field", "Cleve": "progressive_field",
    "KC": "kauffman_stadium", "Kansa": "kauffman_stadium",
    "TB": "tropicana", "Tampa": "tropicana",
    "ATL": "truist_park", "Atlan": "truist_park",
    "DET": "comerica_park", "Detro": "comerica_park",
    "MIA": "loanDepot_park", "Miami": "loanDepot_park",
    "SD": "petco_park", "San D": "petco_park",
    "SEA": "t_mobile_park", "Seatt": "t_mobile_park",
    "SF": "oracle_park", "San F": "oracle_park",
}


def load_park_factors():
    conn = mysql.connector.connect(**config.DB_CONFIG)
    cursor = conn.cursor()

    try:
        for park_id, team, name, hr, hit, run in PARK_DATA:
            cursor.execute("""
                INSERT INTO park_factors
                    (park_id, team_abbr, park_name, hr_factor, hit_factor, run_factor)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    park_name  = VALUES(park_name),
                    hr_factor  = VALUES(hr_factor),
                    hit_factor = VALUES(hit_factor),
                    run_factor = VALUES(run_factor)
            """, (park_id, team, name, hr, hit, run))

        conn.commit()
        log.info(f"Loaded {len(PARK_DATA)} park factor entries")

        # Display
        print(f"\n  {'Park':<30} {'Team':<5} {'Hit':<6} {'HR':<6} {'Run':<6}")
        print(f"  {'-'*30} {'-'*5} {'-'*6} {'-'*6} {'-'*6}")
        for park_id, team, name, hr, hit, run in sorted(PARK_DATA, key=lambda x: x[4], reverse=True):
            indicator = "🔥" if hit > 1.02 else "❄️" if hit < 0.98 else "  "
            print(f"  {name:<30} {team:<5} {hit:<6.2f} {hr:<6.2f} {run:<6.2f} {indicator}")

    finally:
        cursor.close()
        conn.close()


def get_park_factor(home_team: str) -> float:
    """Get hit factor for a home team. Returns 1.0 if not found."""
    park_id = TEAM_ABBR_MAP.get(home_team)
    if not park_id:
        # Try partial match
        for abbr, pid in TEAM_ABBR_MAP.items():
            if home_team.startswith(abbr) or abbr.startswith(home_team[:3]):
                park_id = pid
                break

    if not park_id:
        return 1.0

    for pid, team, name, hr, hit, run in PARK_DATA:
        if pid == park_id:
            return hit

    return 1.0


if __name__ == "__main__":
    load_park_factors()