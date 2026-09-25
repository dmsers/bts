"""
Beat the Streak — Verify Results
=================================
Checks boxscore data for a given date and updates the recommendations
table with actual hit outcomes.

Usage:
    python verify_results.py                    # yesterday
    python verify_results.py --date 2026-04-05  # specific date
"""

import argparse
import logging
import sys
from datetime import date, timedelta

import mysql.connector
import statsapi

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.verify")


def get_db():
    return mysql.connector.connect(**config.DB_CONFIG)


def fetch_hits_for_date(target_date: date) -> tuple:
    """
    Fetch boxscore data for all games on target_date.
    Returns:
        game_hits: {game_pk: {player_id: hits, ...}, ...}
        all_hits:  {player_id: max_hits}  (fallback for recs with no game_pk)
    """
    date_str = target_date.strftime("%Y-%m-%d")
    games = statsapi.schedule(date=date_str)

    game_hits = {}   # per-game: game_pk -> {player_id -> hits}
    all_hits = {}    # flat fallback: player_id -> max hits across games

    for game in games:
        game_pk = game["game_id"]
        status = game.get("status", "")

        if "Final" not in status and "Game Over" not in status:
            log.warning(f"  Skipping {game['away_name']} @ {game['home_name']} — status: {status}")
            continue

        log.info(f"  Processing: {game['away_name']} @ {game['home_name']} (gamePk {game_pk})")

        try:
            box = statsapi.boxscore_data(game_pk)
            game_hits[game_pk] = {}
            for side in ["away", "home"]:
                players = box.get(side, {}).get("players", {})
                for p_key, p_data in players.items():
                    p_id = p_data["person"]["id"]
                    batting = p_data.get("stats", {}).get("batting", {})
                    hits = batting.get("hits", 0)
                    game_hits[game_pk][p_id] = hits
                    # Flat dict keeps max for fallback
                    if p_id not in all_hits or hits > all_hits[p_id]:
                        all_hits[p_id] = hits
        except Exception as e:
            log.error(f"  Could not process boxscore for game {game_pk}: {e}")

    log.info(f"  Collected hit data for {len(all_hits)} players across {len(game_hits)} games")
    return game_hits, all_hits


def resolve_mlb_id(cursor, hitter_id):
    """Try FG ID -> MLB ID crosswalk. Returns MLB ID or None."""
    cursor.execute("""
        SELECT mlb_id FROM player_id_map WHERE fg_id = %s LIMIT 1
    """, (hitter_id,))
    row = cursor.fetchone()
    return row["mlb_id"] if row else None


def lookup_hits(cursor, hitter_id, game_pk, game_hits, all_hits):
    """
    Look up actual hits for a hitter in their specific game.
    Falls back to all-games dict only if game_pk is missing from the rec.
    Returns hit count (int) or None if no match found.
    """
    ids_to_try = [hitter_id]
    mlb_id = resolve_mlb_id(cursor, hitter_id)
    if mlb_id:
        ids_to_try.append(mlb_id)

    # 1) Preferred: check the specific game the recommendation was for
    if game_pk and game_pk in game_hits:
        for pid in ids_to_try:
            if pid in game_hits[game_pk]:
                return game_hits[game_pk][pid]

    # 2) Fallback for recs with missing/wrong game_pk
    for pid in ids_to_try:
        if pid in all_hits:
            log.warning(f"    Fell back to all-games lookup for ID {hitter_id} "
                        f"(game_pk {game_pk} had no match)")
            return all_hits[pid]

    return None


def update_recommendations(target_date: date, game_hits: dict, all_hits: dict):
    """
    Update the recommendations table with actual hit outcomes.
    Uses per-game boxscore data so doubleheaders are handled correctly.
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, hitter_id, hitter_name, game_pk, tier, p_at_least_1
            FROM recommendations
            WHERE game_date = %s
        """, (target_date,))
        recs = cursor.fetchall()

        if not recs:
            log.warning(f"No recommendations found for {target_date}")
            return

        updated = 0
        hits_count = 0
        miss_count = 0

        for rec in recs:
            hits = lookup_hits(
                cursor, rec["hitter_id"], rec["game_pk"],
                game_hits, all_hits
            )

            if hits is not None:
                has_hit = 1 if hits > 0 else 0
                cursor.execute("""
                    UPDATE recommendations
                    SET has_hit = %s, actual_hits = %s
                    WHERE id = %s
                """, (has_hit, hits, rec["id"]))
                updated += 1
                if has_hit:
                    hits_count += 1
                else:
                    miss_count += 1
            else:
                log.warning(f"  No boxscore match for {rec['hitter_name']} "
                            f"(ID: {rec['hitter_id']}, game_pk: {rec['game_pk']})")

        conn.commit()
        log.info(f"Updated {updated} recommendations: {hits_count} hits, {miss_count} misses")

    finally:
        cursor.close()
        conn.close()


def print_report(target_date: date):
    """Print a post-game report showing prediction accuracy."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT `rank`, tier, hitter_name, team_abbr, opp_pitcher_name,
                   p_at_least_1, has_hit, actual_hits
            FROM recommendations
            WHERE game_date = %s
            ORDER BY `rank` ASC
        """, (target_date,))
        recs = cursor.fetchall()

        if not recs:
            print(f"\nNo recommendations found for {target_date}\n")
            return

        verified = [r for r in recs if r["has_hit"] is not None]
        hits = [r for r in verified if r["has_hit"] == 1]
        misses = [r for r in verified if r["has_hit"] == 0]
        unverified = [r for r in recs if r["has_hit"] is None]

        accuracy = len(hits) / len(verified) * 100 if verified else 0

        print(f"\n{'='*75}")
        print(f"  BEAT THE STREAK — POST-GAME REPORT: {target_date}")
        print(f"  Verified: {len(verified)}/{len(recs)} | "
              f"Hits: {len(hits)} | Misses: {len(misses)} | "
              f"Accuracy: {accuracy:.1f}%")
        print(f"{'='*75}")

        print(f"\n  {'Rk':<4}{'Tier':<6}{'P(Hit)':<9}{'Hitter':<24}"
              f"{'vs Pitcher':<20}{'Hits':<6}{'Result'}")
        print(f"  {'-'*75}")

        for r in recs:
            pct = f"{r['p_at_least_1']:.1%}" if r["p_at_least_1"] else "—"
            if r["has_hit"] is None:
                result = "⏳"
            elif r["has_hit"] == 1:
                result = f"✅ {r['actual_hits']}H"
            else:
                result = "❌ 0-fer"

            hits_str = str(r["actual_hits"]) if r["actual_hits"] is not None else "—"

            print(f"  {r['rank']:<4}"
                  f"{r['tier']:<6}"
                  f"{pct:<9}"
                  f"{r['hitter_name'][:22]:<24}"
                  f"{(r['opp_pitcher_name'] or '?')[:18]:<20}"
                  f"{hits_str:<6}"
                  f"{result}")

        # Accuracy by tier
        print(f"\n  Accuracy by Tier:")
        for tier in ["A", "B", "C", "D"]:
            tier_recs = [r for r in verified if r["tier"] == tier]
            if tier_recs:
                tier_hits = len([r for r in tier_recs if r["has_hit"] == 1])
                tier_pct = tier_hits / len(tier_recs) * 100
                print(f"    Tier {tier}: {tier_hits}/{len(tier_recs)} ({tier_pct:.1f}%)")

        print()

    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="BTS Verify Results")
    parser.add_argument(
        "--date", type=str, default=None,
        help="Target date (YYYY-MM-DD). Default: yesterday"
    )
    args = parser.parse_args()

    if args.date:
        from datetime import datetime
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target = date.today() - timedelta(days=1)

    log.info(f"Verifying results for {target}")
    game_hits, all_hits = fetch_hits_for_date(target)

    if game_hits:
        update_recommendations(target, game_hits, all_hits)
        print_report(target)
    else:
        log.warning("No completed games found — try again later")


if __name__ == "__main__":
    main()