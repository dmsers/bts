"""
Beat the Streak — Lineup Fetcher
=================================
Pulls confirmed starting lineups from the MLB Stats API
and populates the daily_lineups table.

Usage:
    python lineups.py                    # today's lineups
    python lineups.py --date 2026-03-28  # specific date
    python lineups.py --show             # show stored lineups
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

import mysql.connector
import statsapi

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.lineups")


def get_db():
    return mysql.connector.connect(**config.DB_CONFIG)

def _get_teams_with_lineups(target_date: date) -> dict:
    """
    Returns {game_pk: set(team_abbr)} for games that already have lineups.
    A team counts as having a lineup if it has 9+ entries.
    """
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT game_pk, team_abbr, COUNT(*) as cnt
            FROM daily_lineups
            WHERE game_date = %s
            GROUP BY game_pk, team_abbr
            HAVING cnt >= 9
        """, (target_date,))
        result = {}
        for game_pk, team_abbr, cnt in cursor.fetchall():
            if game_pk not in result:
                result[game_pk] = set()
            result[game_pk].add(team_abbr)
        return result
    finally:
        cursor.close()
        conn.close()

def fetch_lineups_for_date(target_date: date):
    """
    Pull confirmed lineups from MLB Stats API live game feed.
    Skips games where both teams already have confirmed lineups in the DB.
    """
    log.info(f"Fetching lineups for {target_date}")
    date_str = target_date.strftime("%Y-%m-%d")

    sched = statsapi.schedule(date=date_str)
    if not sched:
        log.warning(f"No games found for {date_str}")
        return []

    # Check which games already have complete lineups
    teams_with_lineups = _get_teams_with_lineups(target_date)

    all_lineups = []
    skipped = 0

    for game in sched:
        game_pk = game["game_id"]
        away_name = game.get("away_name", "?")
        home_name = game.get("home_name", "?")
        away_abbr = config.TEAM_ABBR_MAP.get(away_name, away_name[:5])
        home_abbr = config.TEAM_ABBR_MAP.get(home_name, home_name[:5])

        game_time = ""
        try:
            dt_str = game.get("game_datetime", "")
            if dt_str:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                et = dt - timedelta(hours=4)
                game_time = et.strftime("%-I:%M %p ET")
        except Exception:
            pass

        # Skip if both teams already have lineups for this game
        if away_abbr in teams_with_lineups.get(game_pk, set()) and \
           home_abbr in teams_with_lineups.get(game_pk, set()):
            skipped += 1
            continue

        log.info(f"  Game {game_pk}: {away_name} @ {home_name} — {game_time or 'TBD'}")

        try:
            data = statsapi.get('game', {'gamePk': game_pk})
        except Exception as e:
            log.warning(f"    Could not fetch game data for {game_pk}: {e}")
            continue

        game_data = data.get('gameData', {})
        all_players = game_data.get('players', {})
        live_data = data.get('liveData', {})
        box_teams = live_data.get('boxscore', {}).get('teams', {})

        for side in ["away", "home"]:
            team_name = game.get(f"{side}_name", "")
            team_abbr = config.TEAM_ABBR_MAP.get(team_name, team_name[:5])

            # Skip this side if we already have their lineup
            if team_abbr in teams_with_lineups.get(game_pk, set()):
                continue

            team_box = box_teams.get(side, {})
            batting_order = team_box.get("battingOrder", [])

            if not batting_order:
                log.info(f"    No {side} lineup yet — game at {game_time or 'TBD'}")
                continue

            for i, pid in enumerate(batting_order):
                order = i + 1

                player_info = all_players.get(f"ID{pid}", {})
                player_name = player_info.get("fullName", "Unknown")
                bat_side_info = player_info.get("batSide", {})
                bat_side = bat_side_info.get("code", "R") if isinstance(bat_side_info, dict) else "R"

                team_players = team_box.get("players", {})
                bp = team_players.get(f"ID{pid}", {})
                position = bp.get("position", {}).get("abbreviation", "")

                if bat_side not in ("L", "R", "S"):
                    bat_side = "R"

                all_lineups.append({
                    "game_pk": game_pk,
                    "game_date": target_date,
                    "team_abbr": team_abbr,
                    "hitter_id": pid,
                    "hitter_name": player_name,
                    "batting_order": order,
                    "position": position,
                    "bats": bat_side,
                })

    if skipped:
        log.info(f"  Skipped {skipped} games with complete lineups already cached")
    log.info(f"Found {len(all_lineups)} new lineup entries across {len(sched)} games")
    return all_lineups


def save_lineups(lineups: list):
    if not lineups:
        return

    conn = get_db()
    cursor = conn.cursor()

    try:
        sql = """
            INSERT INTO daily_lineups
                (game_pk, game_date, team_abbr, hitter_id, hitter_name,
                 batting_order, position, bats)
            VALUES
                (%(game_pk)s, %(game_date)s, %(team_abbr)s, %(hitter_id)s,
                 %(hitter_name)s, %(batting_order)s, %(position)s, %(bats)s)
            ON DUPLICATE KEY UPDATE
                batting_order = VALUES(batting_order),
                position = VALUES(position),
                bats = VALUES(bats)
        """
        for lineup in lineups:
            cursor.execute(sql, lineup)

        conn.commit()
        log.info(f"Saved {len(lineups)} lineup entries to database")

    finally:
        cursor.close()
        conn.close()


def show_lineups(target_date: date):
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT dl.game_pk, dl.team_abbr, dl.batting_order,
                   dl.hitter_name, dl.position, dl.bats,
                   g.away_team, g.home_team, g.away_pitcher, g.home_pitcher,
                   g.game_time_et
            FROM daily_lineups dl
            JOIN games g ON g.game_pk = dl.game_pk
            WHERE dl.game_date = %s
            ORDER BY g.game_time_et, dl.game_pk, dl.team_abbr, dl.batting_order
        """, (target_date,))

        rows = cursor.fetchall()
        if not rows:
            print(f"\nNo lineups stored for {target_date}")
            print("Lineups may not be posted yet (usually ~2hrs before game time)\n")
            return

        current_game = None
        for row in rows:
            game_pk, team, order, name, pos, bats, away, home, away_p, home_p, game_time = row

            if game_pk != current_game:
                current_game = game_pk
                time_str = ""
                if game_time:
                    try:
                        t = datetime.strptime(str(game_time), "%H:%M:%S")
                        time_str = t.strftime("%-I:%M %p ET")
                    except Exception:
                        time_str = str(game_time)
                print(f"\n  {'='*55}")
                print(f"  {away} @ {home} — {time_str or 'TBD'}")
                print(f"  Pitchers: {away_p or 'TBD'} vs {home_p or 'TBD'}")
                print(f"  {'='*55}")

            print(f"    {order}. {name:<25} {pos:<4} (Bats: {bats})  [{team}]")

        print(f"\n  Total: {len(rows)} players across {len(set(r[0] for r in rows))} games")

        # Show games still missing lineups
        cursor.execute("""
            SELECT g.game_pk, g.away_team, g.home_team, g.game_time_et
            FROM games g
            WHERE g.game_date = %s
              AND g.game_pk NOT IN (
                  SELECT DISTINCT game_pk FROM daily_lineups WHERE game_date = %s
              )
            ORDER BY g.game_time_et
        """, (target_date, target_date))
        missing = cursor.fetchall()
        if missing:
            print(f"\n  ⏳ Still waiting on lineups:")
            for m in missing:
                time_str = ""
                if m[3]:
                    try:
                        t = datetime.strptime(str(m[3]), "%H:%M:%S")
                        time_str = t.strftime("%-I:%M %p ET")
                    except Exception:
                        time_str = str(m[3])
                print(f"     {m[1]} @ {m[2]} — {time_str or 'TBD'}")
            earliest = min((m[3] for m in missing if m[3]), default=None)
            if earliest:
                try:
                    t = datetime.strptime(str(earliest), "%H:%M:%S")
                    check_time = (t - timedelta(hours=1)).strftime("%-I:%M %p ET")
                    print(f"\n  📋 Try lineups.py again around {check_time}")
                except Exception:
                    pass
        print()

    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="BTS Lineup Fetcher")
    parser.add_argument("--date", type=str, default=None,
                        help="Target date (YYYY-MM-DD). Default: today")
    parser.add_argument("--show", action="store_true",
                        help="Show currently stored lineups (no fetch)")
    args = parser.parse_args()

    target = (datetime.strptime(args.date, "%Y-%m-%d").date()
              if args.date else date.today())

    if args.show:
        show_lineups(target)
    else:
        lineups = fetch_lineups_for_date(target)
        save_lineups(lineups)
        show_lineups(target)


if __name__ == "__main__":
    main()