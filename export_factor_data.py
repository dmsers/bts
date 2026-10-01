"""
Export one row per scored pick with every logged model feature, the game's
home park and the opposing pitcher, for the factor-analysis visuals.

Run from the bts folder:  python export_factor_data.py
Writes:                   data/factor_export.csv  (data/*.csv is gitignored)
"""
import csv
import sys
from pathlib import Path

# Make the bts folder importable so config.py loads from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql.connector
import config

SQL = """
SELECT
    r.id AS rec_id, r.game_date, r.game_pk, r.hitter_id, r.hitter_name, r.team_abbr,
    r.opp_pitcher_name, r.p_at_least_1, r.tier, r.`rank`, r.has_hit, r.actual_hits,
    g.home_team, g.away_team, g.game_time_et,
    CASE WHEN r.team_abbr = g.home_team THEN 1 ELSE 0 END AS is_home,
    pk.park_name, pk.hit_factor AS park_table_hit_factor,
    pf.ba_used, pf.hitter_bats, pf.hitter_k_pct, pf.hitter_pa, pf.games_played,
    pf.hit_rate_blend, pf.pitcher_throws, pf.pitcher_whip, pf.pitcher_era,
    pf.pitcher_baa, pf.pitcher_k_pct, pf.pitcher_matched, pf.batting_order,
    pf.park_hit_factor, pf.team_factor, pf.has_lineup, pf.is_doubleheader,
    pf.recent_form_games, pf.recent_form_hits, pf.recent_form_streak,
    pf.pitcher_factor, pf.platoon_bonus, pf.contact_bonus, pf.form_bonus,
    pf.pa_confidence, pf.p_hit_per_ab, pf.expected_pa, pf.expected_ab
FROM recommendations r
LEFT JOIN prediction_features pf ON pf.recommendation_id = r.id
LEFT JOIN games g ON g.game_pk = r.game_pk
LEFT JOIN park_factors pk ON pk.team_abbr = g.home_team
WHERE r.has_hit IS NOT NULL
ORDER BY r.game_date, r.`rank`
"""

def main():
    out = Path(config.DATA_DIR) / "factor_export.csv"
    conn = mysql.connector.connect(**config.DB_CONFIG)
    cur = conn.cursor()
    cur.execute(SQL)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    with_feat = sum(1 for r in rows if r[cols.index("ba_used")] is not None)
    print(f"Wrote {len(rows)} picks ({with_feat} with logged features) to {out}")

if __name__ == "__main__":
    main()
