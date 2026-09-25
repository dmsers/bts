"""
validate_pitcher_teams.py
─────────────────────────
Drop-in validation to call from recommend.py BEFORE writing final
recommendations.  Detects and resolves cases where the same pitcher
appears with different team assignments.

Usage in recommend.py:
    from validate_pitcher_teams import validate_and_fix_pitcher_teams
    recommendations = validate_and_fix_pitcher_teams(recommendations, conn)
"""

import logging
from collections import defaultdict
from datetime import date

logger = logging.getLogger("bts.validate")


def validate_and_fix_pitcher_teams(recommendations, conn):
    """
    Scan a list of recommendation dicts for pitchers assigned to
    multiple teams.  Resolve by trusting the schedule (game-day
    source of truth) over stats-table team fields.

    Parameters
    ----------
    recommendations : list[dict]
        Each dict must have at least: pitcher_name, pitcher_team
        May also have: pitcher_mlbam_id, game_id, source, etc.
    conn : mysql.connector connection

    Returns
    -------
    list[dict]  — cleaned recommendations (same list, mutated in place)
    """
    # ── group by pitcher name ──────────────────────────────────────
    by_pitcher = defaultdict(list)
    for rec in recommendations:
        by_pitcher[rec["opp_pitcher"]].append(rec)

    conflicts_found = 0

    for name, recs in by_pitcher.items():
        teams = set(r["pitcher_team"] for r in recs)
        if len(teams) <= 1:
            continue

        conflicts_found += 1
        logger.warning(
            "CONFLICT: %s listed with teams: %s",
            name, ", ".join(sorted(teams))
        )

        # ── resolve: trust today's schedule ────────────────────────
        correct_team = _resolve_team_from_schedule(conn, name)

        if correct_team:
            logger.info("  → Resolved %s to team %s via schedule", name, correct_team)
            for rec in recs:
                if rec["pitcher_team"] != correct_team:
                    logger.info(
                        "  → Fixing: %s vs %s [%s → %s] (hitter: %s)",
                        name, rec["pitcher_team"], rec["pitcher_team"],
                        correct_team, rec.get("hitter_name", "?")
                    )
                    rec["pitcher_team"] = correct_team
        else:
            # Fallback: trust the source tied to lineups (📊) over
            # the stats-only source (📋), if your recs carry that flag
            logger.warning(
                "  → Could not resolve %s from schedule; "
                "flagging for manual review", name
            )
            for rec in recs:
                rec["_team_conflict"] = True  # flag for downstream

    if conflicts_found == 0:
        logger.info("Pitcher team validation passed — no conflicts.")
    else:
        logger.warning("Resolved %d pitcher team conflict(s).", conflicts_found)

    return recommendations


def _resolve_team_from_schedule(conn, pitcher_name):
    """
    Look up today's schedule to find the canonical team for a pitcher.
    The schedule comes from the MLB Stats API via ingest.py, so it
    should be the most authoritative game-day source.
    """
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT away_team, home_team, away_pitcher, home_pitcher
            FROM games
            WHERE game_date = %s
              AND (away_pitcher LIKE %s OR home_pitcher LIKE %s)
            LIMIT 1
        """, (date.today().isoformat(),
              f"%{pitcher_name}%", f"%{pitcher_name}%"))
        row = cur.fetchone()
    finally:
        cur.close()

    if not row:
        return None

    if pitcher_name.lower() in (row.get("away_pitcher") or "").lower():
        return row["away_team"]
    if pitcher_name.lower() in (row.get("home_pitcher") or "").lower():
        return row["home_team"]
    return None


def _resolve_team_from_player_id_map(conn, pitcher_name):
    """
    Fallback: check player_id_map for the pitcher's current team.
    Less reliable than schedule if the map hasn't been refreshed.
    """
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT team
            FROM player_id_map
            WHERE player_name LIKE %s
            LIMIT 1
        """, (f"%{pitcher_name}%",))
        row = cur.fetchone()
    finally:
        cur.close()

    return row["team"] if row else None