"""
Beat the Streak — Pick Recommender (v0.1 heuristic)
====================================================
Ranks all hitters playing on a given date by estimated P(at least 1 hit).

Usage:
    python recommend.py                    # tomorrow's games
    python recommend.py --date 2026-03-28  # specific date
    python recommend.py --date 2026-03-28 --top 60
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from itertools import groupby

import mysql.connector

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bts.recommend")


def get_db():
    return mysql.connector.connect(**config.DB_CONFIG)


def get_games(cursor, target_date: date):
    cursor.execute("""
        SELECT 
            g.game_pk, g.game_date, g.away_team, g.home_team, g.game_time_et,
            g.away_pitcher_id, g.away_pitcher, g.home_pitcher_id, g.home_pitcher,
            COALESCE(p.hit_factor, 1.0) AS park_hit_factor
        FROM games g
        LEFT JOIN park_factors p ON g.home_team = p.team_abbr
        WHERE g.game_date = %s AND g.status = 'scheduled'
    """, (target_date,))
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_pitcher_whip(cursor, pitcher_id: int, pitcher_name: str = None) -> dict:
    defaults = {"pitcher_name": pitcher_name or "Unknown", "throws": "R",
                "whip": 1.30, "era": 4.00, "k_pct": 0.20, "ba_against": 0.250,
                "matched": False}

    if not pitcher_id and not pitcher_name:
        return defaults

    seasons = [config.CURRENT_SEASON, config.PREVIOUS_SEASON]
    min_ip_current = 15
    min_ip_prev = 20

    for i, season in enumerate(seasons):
        min_ip = min_ip_current if i == 0 else min_ip_prev
        
        if pitcher_id:
            cursor.execute("""
                SELECT ps.pitcher_name, ps.throws, ps.whip, ps.era, ps.k_pct, ps.ba_against
                FROM player_id_map pim
                JOIN pitcher_stats ps ON ps.pitcher_id = pim.fg_id
                WHERE pim.mlb_id = %s
                  AND ps.season = %s
                  AND ps.ip >= %s
                ORDER BY ps.ip DESC
                LIMIT 1
            """, (pitcher_id, season, min_ip))
            row = cursor.fetchone()
            if row:
                result = dict(zip(
                    ["pitcher_name", "throws", "whip", "era", "k_pct", "ba_against"], row
                ))
                result["matched"] = True
                return result

        if pitcher_name and pitcher_name != "TBD":
            parts = pitcher_name.strip().split()
            if len(parts) >= 2:
                first_name = parts[0]
                last_name = parts[-1]

                cursor.execute("""
                    SELECT pitcher_name, throws, whip, era, k_pct, ba_against
                    FROM pitcher_stats
                    WHERE LOWER(pitcher_name) LIKE LOWER(%s)
                      AND LOWER(pitcher_name) LIKE LOWER(%s)
                      AND season = %s
                      AND ip >= %s
                    ORDER BY ip DESC
                    LIMIT 1
                """, (f"{first_name}%", f"%{last_name}%", season, min_ip))
                row = cursor.fetchone()
                if row:
                    result = dict(zip(
                        ["pitcher_name", "throws", "whip", "era", "k_pct", "ba_against"], row
                    ))
                    result["matched"] = True
                    return result

    return defaults

def get_lineups_for_game(cursor, game_pk: int, side: str):
    cursor.execute("""
        SELECT away_team, home_team FROM games WHERE game_pk = %s
    """, (game_pk,))
    row = cursor.fetchone()
    if not row:
        return []

    team = row[0] if side == "away" else row[1]

    cursor.execute("""
        SELECT hitter_id, hitter_name, batting_order, bats
        FROM daily_lineups
        WHERE game_pk = %s AND team_abbr = %s
        ORDER BY batting_order
    """, (game_pk, team))

    cols = ["hitter_id", "hitter_name", "batting_order", "bats"]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


def has_lineups(cursor, target_date: date) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM daily_lineups WHERE game_date = %s",
        (target_date,)
    )
    return cursor.fetchone()[0] > 0


def get_eligible_hitters(cursor, min_pa_current: int = 50, min_pa_prev: int = 200):
    """
    Load hitters from both current and previous seasons.
    Prioritizes current season but blends with previous if current sample is small.
    """
    # 1. Load 2025 hitters (Baseline)
    cursor.execute("""
        SELECT hitter_id, hitter_name, bats, pa, ab, ba, obp, k_pct, contact_pct,
               ba_vs_l, ba_vs_r, games_with_hit, games_played, hit_rate
        FROM hitter_stats
        WHERE season = %s AND pa >= %s
    """, (config.PREVIOUS_SEASON, min_pa_prev))
    cols = [d[0] for d in cursor.description]
    prev_hitters = {r[0]: dict(zip(cols, r)) for r in cursor.fetchall()}

    # 2. Load 2026 hitters (Current form)
    # Lower threshold for current season
    cursor.execute("""
        SELECT hitter_id, hitter_name, bats, pa, ab, ba, obp, k_pct, contact_pct,
               ba_vs_l, ba_vs_r, games_with_hit, games_played, hit_rate
        FROM hitter_stats
        WHERE season = %s AND pa >= 20
    """, (config.CURRENT_SEASON,))
    curr_hitters = {r[0]: dict(zip(cols, r)) for r in cursor.fetchall()}

    # 3. Blend them
    blended = {}
    all_ids = set(prev_hitters.keys()) | set(curr_hitters.keys())

    for hid in all_ids:
        c = curr_hitters.get(hid)
        p = prev_hitters.get(hid)

        if not p and c:
            if c["pa"] >= min_pa_current:
                blended[hid] = c
            continue
        if p and not c:
            blended[hid] = p
            continue
        
        # Both exist: Blend
        # Bayesian-ish weighting: prior fades as current sample grows
        # At 50 PA: prior=75, At 150 PA: prior=25, At 200+ PA: prior=0
        c_pa = float(c["pa"])
        prior_weight = max(0, 100 - c_pa * 0.5)
        total_weight = c_pa + prior_weight

        # Fields to blend
        b = c.copy()
        if total_weight > 0:
            for field in ["ab", "ba", "obp", "k_pct", "contact_pct", "ba_vs_l", "ba_vs_r"]:
                cv = float(c.get(field) or 0)
                pv = float(p.get(field) or 0)
                if cv and pv:
                    b[field] = (cv * c_pa + pv * prior_weight) / total_weight
                elif cv:
                    b[field] = cv
                else:
                    b[field] = pv
        
        # Keep current-season hit_rate and games_played unblended —
        # these reflect current form and are used as a direct signal
        b["hit_rate"] = c.get("hit_rate") or p.get("hit_rate")
        b["games_played"] = c.get("games_played") or p.get("games_played")

        # Preserve real bats hand from current season; fall back to prev
        b["bats"] = c.get("bats") or p.get("bats")

        b["pa"] = c_pa + float(p["pa"])  # combined total for confidence calc
        blended[hid] = b

    log.info(f"Blended {len(blended)} hitters using {config.CURRENT_SEASON} and {config.PREVIOUS_SEASON} stats")
    return blended


# ---------------------------------------------------------------------------
# Doubleheader detection
# ---------------------------------------------------------------------------

def detect_doubleheaders(games):
    """
    Identify teams playing in doubleheaders on the same date.
    Returns dict: team_abbr -> [game1, game2, ...] for teams with 2+ games.
    """
    team_games = {}
    for game in games:
        for side in ["away", "home"]:
            team = game[f"{side}_team"]
            if team not in team_games:
                team_games[team] = []
            team_games[team].append(game)

    return {team: glist for team, glist in team_games.items() if len(glist) >= 2}


def get_other_game_prob(cursor, hitter, team, current_game_pk,
                        doubleheader_teams, batting_order, park_hit_factor,
                        team_quality=None):
    """
    For a hitter in a doubleheader, estimate P(at least 1 hit) in the OTHER game.
    Returns the single-game p_at_least_1 for the other game, or None if not a DH.
    """
    if team not in doubleheader_teams:
        return None

    other_games = [g for g in doubleheader_teams[team]
                   if g["game_pk"] != current_game_pk]
    if not other_games:
        return None

    other_game = other_games[0]

    # Figure out which pitcher the hitter faces in the other game
    if team == other_game["away_team"]:
        opp_pitcher_id = other_game["home_pitcher_id"]
        opp_pitcher_name = other_game["home_pitcher"]
    else:
        opp_pitcher_id = other_game["away_pitcher_id"]
        opp_pitcher_name = other_game["away_pitcher"]

    other_pitcher = get_pitcher_whip(cursor, opp_pitcher_id, opp_pitcher_name)
    recent_form = get_recent_form(cursor, hitter.get("hitter_id") or 0)

    # Same park for both games of a doubleheader
    other_prob = estimate_hit_probability(
        hitter, other_pitcher, batting_order, park_hit_factor,
        team_quality=team_quality, recent_form=recent_form
    )
    return other_prob["p_at_least_1"]


# ---------------------------------------------------------------------------
# Roster lookup for projected pools
# ---------------------------------------------------------------------------

def get_team_fg_ids(cursor, team_abbr: str) -> set:
    """
    Get the set of FanGraphs hitter IDs for an MLB team's active roster.
    Uses the MLB Stats API to pull the roster, then maps MLB IDs → FG IDs
    via player_id_map.
    """
    try:
        import statsapi
    except ImportError:
        log.warning("statsapi not available — cannot filter projected pools by roster")
        return set()

    # Reverse-lookup: team abbreviation → MLB team ID
    team_id = _get_mlb_team_id(team_abbr)
    if not team_id:
        log.warning(f"  Could not resolve team ID for {team_abbr}")
        return set()

    try:
        roster = statsapi.roster(team_id, rosterType="active")
    except Exception as e:
        log.warning(f"  Failed to fetch roster for {team_abbr}: {e}")
        return set()

    # Use the lower-level get() for structured JSON
    try:
        data = statsapi.get("team_roster", {"teamId": team_id, "rosterType": "active"})
        mlb_ids = []
        for entry in data.get("roster", []):
            person = entry.get("person", {})
            pid = person.get("id")
            pos = entry.get("position", {}).get("abbreviation", "")
            # Skip pitchers
            if pos == "P":
                continue
            if pid:
                mlb_ids.append(pid)
    except Exception as e:
        log.warning(f"  Failed to parse roster for {team_abbr}: {e}")
        return set()

    if not mlb_ids:
        return set()

    # Batch lookup: MLB IDs → FG IDs via player_id_map
    placeholders = ",".join(["%s"] * len(mlb_ids))
    cursor.execute(f"""
        SELECT mlb_id, fg_id FROM player_id_map
        WHERE mlb_id IN ({placeholders})
    """, mlb_ids)

    fg_ids = set()
    mapped = 0
    for mlb_id, fg_id in cursor.fetchall():
        if fg_id:
            fg_ids.add(fg_id)
            mapped += 1

    log.info(f"  Roster {team_abbr}: {len(mlb_ids)} position players, {mapped} mapped to FG IDs")
    return fg_ids


# Cache team ID lookups to avoid repeated API calls
_team_id_cache = {}

def _get_mlb_team_id(team_abbr: str) -> int:
    """Resolve a team abbreviation to its MLB Stats API team ID."""
    if team_abbr in _team_id_cache:
        return _team_id_cache[team_abbr]

    try:
        import statsapi
        teams = statsapi.get("teams", {"sportIds": 1})
        for t in teams.get("teams", []):
            abbr = t.get("abbreviation", "")
            full_name = t.get("name", "")
            mapped_abbr = config.TEAM_ABBR_MAP.get(full_name, abbr)

            if abbr == team_abbr or mapped_abbr == team_abbr:
                _team_id_cache[team_abbr] = t["id"]
                return t["id"]
    except Exception as e:
        log.warning(f"  Team ID lookup failed for {team_abbr}: {e}")

    return None


def get_team_quality_map(cursor, season: int):
    """
    Calculate aggregate pitching quality for each team.
    Used as a secondary signal for hitters facing weak overall staffs.
    """
    cursor.execute("""
        SELECT team, AVG(whip), AVG(era)
        FROM (
            SELECT away_team as team, away_pitcher as p_name FROM games WHERE away_pitcher != 'TBD'
            UNION
            SELECT home_team as team, home_pitcher as p_name FROM games WHERE home_pitcher != 'TBD'
        ) g
        JOIN pitcher_stats ps ON g.p_name = ps.pitcher_name
        WHERE ps.season = %s
        GROUP BY team
    """, (season,))
    
    quality = {}
    for team, whip, era in cursor.fetchall():
        quality[team] = {
            "avg_whip": float(whip or 1.30),
            "avg_era": float(era or 4.00)
        }
    return quality


def get_recent_form(cursor, hitter_id: int, days: int = 5) -> dict:
    """
    Check the results table for the hitter's performance in the last few games.
    Returns a dict with hit_count, game_count, and streak.
    """
    cursor.execute("""
        SELECT got_hit FROM results
        WHERE hitter_id = %s
        ORDER BY game_date DESC
        LIMIT %s
    """, (hitter_id, days))
    rows = cursor.fetchall()
    if not rows:
        return {"games": 0, "hits_in_games": 0, "streak": 0}

    hits_in_games = sum(r[0] for r in rows)
    
    # Calculate current hit streak
    streak = 0
    for r in rows:
        if r[0]:
            streak += 1
        else:
            break
            
    return {"games": len(rows), "hits_in_games": hits_in_games, "streak": streak}


def estimate_hit_probability(
        hitter: dict, 
        pitcher: dict, 
        batting_order: int = 5, 
        park_hit_factor: float = 1.0,
        team_quality: dict = None,
        recent_form: dict = None
) -> dict:
    """
    Estimate P(at least 1 hit) for a hitter in a single game.
    """
    # 1. Base BA — prefer platoon split if available
    # League average BA baseline
    ba = float(hitter.get("ba") or config.LEAGUE_BA_BASELINE)

    pitcher_throws = pitcher.get("throws", "R")
    hitter_bats = hitter.get("bats")

    # Apply platoon BA split
    if pitcher_throws == "L" and hitter.get("ba_vs_l"):
        ba = float(hitter["ba_vs_l"])
    elif pitcher_throws == "R" and hitter.get("ba_vs_r"):
        ba = float(hitter["ba_vs_r"])

    # 2. Pitcher difficulty
    # League average BAA baseline
    pitcher_baa = float(pitcher.get("ba_against") or config.LEAGUE_BAA_BASELINE)
    pitcher_baa = min(pitcher_baa, 0.400)
    
    # Primary signal: Hitter talent vs Pitcher's ability to prevent hits
    pitcher_factor = (pitcher_baa / config.LEAGUE_BAA_BASELINE) ** 1.1

    # Pitcher K-rate penalty
    pitcher_k_pct = float(pitcher.get("k_pct") or 0.20)
    pitcher_k_adjustment = 1.0 - (pitcher_k_pct - 0.20) * 0.60
    pitcher_k_adjustment = max(pitcher_k_adjustment, 0.88)
    pitcher_k_adjustment = min(pitcher_k_adjustment, 1.06)
    pitcher_factor *= pitcher_k_adjustment

    # Team quality factor
    team_factor = 1.0
    if team_quality:
        avg_whip = team_quality.get("avg_whip", 1.30)
        team_factor = 1.0 + (avg_whip - 1.30) * 0.10
        team_factor = max(0.95, min(1.05, team_factor))

    # 3. Platoon bonus
    platoon_bonus = 1.0
    if hitter_bats == "S":
        platoon_bonus = 1.03
    elif hitter_bats and hitter_bats != pitcher_throws:
        platoon_bonus = 1.05
    elif hitter_bats and hitter_bats == pitcher_throws:
        platoon_bonus = 0.96  # same-side penalty

    # 4. Contact bonus
    k_pct = float(hitter.get("k_pct") or 0.20)
    contact_bonus = 1.0 + (0.20 - k_pct) * 1.2
    contact_bonus = max(contact_bonus, 0.82)

    # 5. Recent Form bonus (Hot/Cold)
    form_bonus = 1.0
    if recent_form and recent_form["games"] >= 3:
        # Every game with a hit in last 5 adds 1% bonus; streak of 3+ adds 2%
        success_rate = recent_form["hits_in_games"] / recent_form["games"]
        form_bonus = 1.0 + (success_rate - 0.70) * 0.15 # Neutral at 70% success
        if recent_form["streak"] >= 3:
            form_bonus += 0.02
        form_bonus = max(0.94, min(1.06, form_bonus))

    # 6. Sample-size confidence factor
    pa = float(hitter.get("pa") or 200)
    pa_confidence = min(1.0, 0.97 + 0.03 * (pa - 200) / 300)

    # 7. Park factor
    safe_park_factor = float(park_hit_factor or 1.0)

    # 8. Model hit probability per At-Bat
    p_hit_ab = ba * pitcher_factor * platoon_bonus * contact_bonus * safe_park_factor * pa_confidence * team_factor * form_bonus
    p_hit_ab = min(p_hit_ab, 0.450)

    # 9. Convert to P(at least 1 hit) over expected At-Bats
    expected_pa = config.EXPECTED_PA_BY_ORDER.get(batting_order, 4.2)
    
    # Estimate AB/PA ratio
    h_pa = float(hitter.get("pa") or 1)
    h_ab = float(hitter.get("ab") or h_pa * 0.9)
    # Factor in pitcher's walk tendency
    pitcher_bb_pct = float(pitcher.get("bb_pct") or 0.085)
    walk_adj = 1.0 - (pitcher_bb_pct - 0.085)
    
    ab_per_pa = (h_ab / h_pa if h_pa > 0 else 0.9) * walk_adj
    expected_ab = expected_pa * ab_per_pa
    
    p_at_least_1_model = 1.0 - (1.0 - p_hit_ab) ** expected_ab

    # 10. Blend with empirical hit_rate when we have enough data
    hit_rate = float(hitter.get("hit_rate") or 0)
    hit_rate = min(hit_rate, 0.92) 
    games_played = float(hitter.get("games_played") or 0)

    if hit_rate > 0 and games_played >= 15:
        empirical_weight = min(0.60, 0.30 + 0.004 * games_played) # Reduced weight on small samples
        p_at_least_1 = (1.0 - empirical_weight) * p_at_least_1_model + empirical_weight * hit_rate
    else:
        p_at_least_1 = p_at_least_1_model

    return {
        "p_hit_per_ab": round(p_hit_ab, 4),
        "expected_pa": expected_pa,
        "expected_ab": round(expected_ab, 2),
        "p_at_least_1": round(p_at_least_1, 4),
        "pitcher_factor": round(pitcher_factor, 3),
        "form_bonus": round(form_bonus, 3),
        "platoon_bonus": round(platoon_bonus, 3),
        "contact_bonus": round(contact_bonus, 3),
        "hit_rate_blend": round(hit_rate, 4) if hit_rate > 0 else None,
        # --- raw/intermediate inputs, logged for future model training ---
        "ba_used": round(ba, 4),
        "pitcher_baa": round(pitcher_baa, 4),
        "pitcher_k_pct_input": round(pitcher_k_pct, 4),
        "team_factor": round(team_factor, 3),
        "pa_confidence": round(pa_confidence, 3),
        "park_hit_factor_used": round(safe_park_factor, 3),
        "batting_order": batting_order,
        "hitter_pa": round(pa, 1),
        "games_played": int(games_played) if games_played else None,
        "recent_form_games": recent_form["games"] if recent_form else None,
        "recent_form_hits": recent_form["hits_in_games"] if recent_form else None,
        "recent_form_streak": recent_form["streak"] if recent_form else None,
    }


def assign_tier(p_at_least_1: float) -> str:
    if p_at_least_1 >= config.TIER_THRESHOLDS["A"]:
        return "A"
    elif p_at_least_1 >= config.TIER_THRESHOLDS["B"]:
        return "B"
    elif p_at_least_1 >= config.TIER_THRESHOLDS["C"]:
        return "C"
    return "D"


def build_recommendations(target_date: date, top_n: int = 60):
    conn = get_db()
    cursor = conn.cursor()

    try:
        games = get_games(cursor, target_date)
        if not games:
            log.warning(f"No games found for {target_date}. Run ingest.py first!")
            return []

        log.info(f"Found {len(games)} games for {target_date}")

        # ── Detect doubleheaders ──
        doubleheader_teams = detect_doubleheaders(games)
        if doubleheader_teams:
            dh_names = ", ".join(doubleheader_teams.keys())
            log.info(f"Doubleheader detected for: {dh_names}")
            log.info(f"  BTS rule: hitter must get a hit in BOTH games — "
                     f"compound probability applied")

        hitters = get_eligible_hitters(cursor)
        log.info(f"Loaded {len(hitters)} blended hitter stats")

        # ── Team Quality Map ──
        team_quality_map = get_team_quality_map(cursor, config.CURRENT_SEASON)

        recommendations = []
        seen_hitters_global = set()

        # ── PASS 1: Confirmed lineups first ──
        lineup_recs = []
        projected_games = []

        for game in games:
            current_park_factor = float(game.get("park_hit_factor", 1.0))
            for side in ["away", "home"]:
                team = game[f"{side}_team"]
                opp_side = 'home' if side == 'away' else 'away'
                opp_team = game[f"{opp_side}_team"]
                opp_pitcher_id = game[f"{opp_side}_pitcher_id"]
                opp_pitcher_name = game[f"{opp_side}_pitcher"]

                pitcher = get_pitcher_whip(cursor, opp_pitcher_id, opp_pitcher_name)
                lineup = get_lineups_for_game(cursor, game["game_pk"], side)

                if lineup:
                    for player in lineup:
                        hid = player["hitter_id"]  # MLB ID from daily_lineups

                        # Translate MLB ID → FG ID via crosswalk
                        cursor.execute("""
                            SELECT fg_id FROM player_id_map WHERE mlb_id = %s LIMIT 1
                        """, (hid,))
                        fg_row = cursor.fetchone()
                        fg_id = fg_row[0] if fg_row else None

                        # Skip if already seen under either ID
                        if fg_id and fg_id in seen_hitters_global:
                            continue
                        if hid in seen_hitters_global:
                            continue

                        # Look up season stats — try FG ID first, then MLB ID, then name
                        h = None
                        if fg_id:
                            h = hitters.get(fg_id)
                        if not h:
                            h = hitters.get(hid)
                        if not h:
                            pname = player["hitter_name"]
                            last_name = pname.strip().split()[-1]
                            first_initial = pname.strip().split()[0][0] if pname.strip().split() else ""
                            lookup_min_pa = 20
                            cursor.execute("""
                                SELECT hitter_id, hitter_name, bats, pa, ab, ba, obp,
                                       k_pct, contact_pct, ba_vs_l, ba_vs_r,
                                       games_with_hit, games_played, hit_rate
                                FROM hitter_stats
                                WHERE LOWER(hitter_name) LIKE LOWER(%s)
                                  AND LOWER(hitter_name) LIKE LOWER(%s)
                                  AND pa >= %s
                                  AND season IN (%s, %s)
                                ORDER BY season DESC, pa DESC
                                LIMIT 1
                            """, (f"{first_initial}%", f"%{last_name}%", lookup_min_pa, config.CURRENT_SEASON, config.PREVIOUS_SEASON))
                            row = cursor.fetchone()
                            if row:
                                cols = [d[0] for d in cursor.description]
                                h = dict(zip(cols, row))

                        if not h:
                            h = {
                                "hitter_name": player["hitter_name"],
                                "bats": player.get("bats", "R"),
                                "ba": 0.250, "k_pct": 0.20,
                                "contact_pct": 0.80,
                                "ba_vs_l": None, "ba_vs_r": None,
                            }

                        batting_order = player["batting_order"]
                        h_form = get_recent_form(cursor, h.get("hitter_id") or 0)
                        prob = estimate_hit_probability(
                            h, pitcher, batting_order, 
                            park_hit_factor=current_park_factor,
                            team_quality=team_quality_map.get(opp_team),
                            recent_form=h_form
                        )

                        # ── Doubleheader compound probability ──
                        is_dh = team in doubleheader_teams
                        p_single_game = prob["p_at_least_1"]
                        p_other_game = None

                        if is_dh:
                            p_other_game = get_other_game_prob(
                                cursor, h, team, game["game_pk"],
                                doubleheader_teams, batting_order,
                                current_park_factor,
                                team_quality=team_quality_map.get(opp_team)
                            )
                            if p_other_game is not None:
                                # BTS requires hit in BOTH games
                                prob["p_at_least_1"] = round(
                                    p_single_game * p_other_game, 4
                                )
                            else:
                                # Can't determine other game pitcher — use conservative
                                # estimate: assume ~70% chance of hit in unknown game
                                prob["p_at_least_1"] = round(
                                    p_single_game * 0.70, 4
                                )

                        lineup_recs.append({
                            "game_pk": game["game_pk"],
                            "game_date": target_date,
                            "game_time": game.get("game_time_et"),
                            "hitter_id": hid,
                            "hitter_name": player["hitter_name"],
                            "team": team,
                            "pitcher_team": game[f"{'away' if side == 'home' else 'home'}_team"],
                            "hitter_ba": h.get("ba"),
                            "hitter_k_pct": h.get("k_pct"),
                            "bats": player.get("bats", h.get("bats", "?")),
                            "opp_pitcher": pitcher["pitcher_name"],
                            "opp_pitcher_id": opp_pitcher_id,
                            "pitcher_throws": pitcher.get("throws", "?"),
                            "pitcher_whip": pitcher.get("whip"),
                            "pitcher_era": pitcher.get("era"),
                            "pitcher_matched": pitcher.get("matched", False),
                            "has_lineup": True,
                            "is_doubleheader": is_dh,
                            "p_single_game": p_single_game if is_dh else None,
                            "p_other_game": p_other_game if is_dh else None,
                            **prob,
                            "tier": assign_tier(prob["p_at_least_1"]),
                            "matchup": f"{player['hitter_name']} vs {pitcher['pitcher_name']}",
                        })

                         # Claim all known IDs so Pass 2 skips this player
                        seen_hitters_global.add(hid)
                        if fg_id:
                            seen_hitters_global.add(fg_id)
                        if h and h.get("hitter_id") and h["hitter_id"] != hid:
                            seen_hitters_global.add(h["hitter_id"])
                else:
                    projected_games.append((game, side, team, pitcher, opp_pitcher_id, current_park_factor))

        recommendations.extend(lineup_recs)

        # ── PASS 2: Projected pools (roster-filtered) ──
        roster_cache = {}

        for game, side, team, pitcher, opp_pitcher_id, current_park_factor in projected_games:
            game_recs = []

            if team not in roster_cache:
                roster_cache[team] = get_team_fg_ids(cursor, team)
            team_fg_ids = roster_cache[team]

            if not team_fg_ids:
                log.warning(f"  No roster FG IDs for {team} — skipping projected pool")
                continue

            for hid, h in hitters.items():
                if hid in seen_hitters_global:
                    continue

                if hid not in team_fg_ids:
                    continue

                opp_side = 'home' if side == 'away' else 'away'
                opp_team = game[f"{opp_side}_team"]

                h_form = get_recent_form(cursor, h.get("hitter_id") or 0)
                prob = estimate_hit_probability(
                    h, pitcher, batting_order=5, 
                    park_hit_factor=current_park_factor,
                    team_quality=team_quality_map.get(opp_team),
                    recent_form=h_form
                )

                # ── Doubleheader compound probability ──
                is_dh = team in doubleheader_teams
                p_single_game = prob["p_at_least_1"]
                p_other_game = None

                if is_dh:
                    p_other_game = get_other_game_prob(
                        cursor, h, team, game["game_pk"],
                        doubleheader_teams, 5,  # default batting order for projected
                        current_park_factor,
                        team_quality=team_quality_map.get(opp_team)
                    )
                    if p_other_game is not None:
                        prob["p_at_least_1"] = round(
                            p_single_game * p_other_game, 4
                        )
                    else:
                        prob["p_at_least_1"] = round(
                            p_single_game * 0.70, 4
                        )

                game_recs.append({
                    "game_pk": game["game_pk"],
                    "game_date": target_date,
                    "game_time": game.get("game_time_et"),
                    "hitter_id": hid,
                    "hitter_name": h["hitter_name"],
                    "team": team,
                    "pitcher_team": game[f"{'away' if side == 'home' else 'home'}_team"],
                    "hitter_ba": h.get("ba"),
                    "hitter_k_pct": h.get("k_pct"),
                    "bats": h.get("bats", "?"),
                    "opp_pitcher": pitcher["pitcher_name"],
                    "opp_pitcher_id": opp_pitcher_id,
                    "pitcher_throws": pitcher.get("throws", "?"),
                    "pitcher_whip": pitcher.get("whip"),
                    "pitcher_era": pitcher.get("era"),
                    "pitcher_matched": pitcher.get("matched", False),
                    "has_lineup": False,
                    "is_doubleheader": is_dh,
                    "p_single_game": p_single_game if is_dh else None,
                    "p_other_game": p_other_game if is_dh else None,
                    **prob,
                    "tier": assign_tier(prob["p_at_least_1"]),
                    "matchup": f"{h['hitter_name']} vs {pitcher['pitcher_name']}",
                })

            game_recs.sort(key=lambda x: x["p_at_least_1"], reverse=True)
            count = 0
            for rec in game_recs:
                if rec["hitter_id"] not in seen_hitters_global and count < 3:
                    recommendations.append(rec)
                    seen_hitters_global.add(rec["hitter_id"])
                    count += 1

        # Sort all picks by P(at least 1 hit) descending
        recommendations.sort(key=lambda x: x["p_at_least_1"], reverse=True)

        # Assign rankings
        for i, rec in enumerate(recommendations):
            rec["rank"] = i + 1

        # Apply per-pitcher cap + soft concentration penalty
        # v0.4: hard cap at 3 (was 2), plus a 3% penalty on the 2nd pick
        # and a 5% penalty on the 3rd pick vs the same arm.
        max_per_pitcher = 3
        concentration_penalty_2 = 0.97  # 3% haircut for 2nd pick
        concentration_penalty_3 = 0.95  # 5% haircut for 3rd pick
        capped = []
        pitcher_counts = {}
        for rec in recommendations:
            p_name = rec["opp_pitcher"]
            pitcher_counts[p_name] = pitcher_counts.get(p_name, 0) + 1
            if pitcher_counts[p_name] > max_per_pitcher:
                continue  # hard cap
            if pitcher_counts[p_name] == 2:
                rec["p_at_least_1"] = round(
                    rec["p_at_least_1"] * concentration_penalty_2, 4
                )
                rec["tier"] = assign_tier(rec["p_at_least_1"])
            elif pitcher_counts[p_name] == 3:
                rec["p_at_least_1"] = round(
                    rec["p_at_least_1"] * concentration_penalty_3, 4
                )
                rec["tier"] = assign_tier(rec["p_at_least_1"])
            capped.append(rec)

        # Re-sort after concentration penalties, then re-rank
        capped.sort(key=lambda x: x["p_at_least_1"], reverse=True)
        for i, rec in enumerate(capped):
            rec["rank"] = i + 1

        return capped[:top_n]

    finally:
        cursor.close()
        conn.close()


def print_recommendations(recs):
    if not recs:
        print("\nNo recommendations available. Run ingest.py first!\n")
        return

    print(f"\n{'='*110}")
    print(f"  BEAT THE STREAK — TOP PICKS for {recs[0]['game_date']}")
    print(f"{'='*110}")

    # Check if any doubleheaders
    dh_teams = set(r["team"] for r in recs if r.get("is_doubleheader"))
    if dh_teams:
        print(f"\n  ⚠️  DOUBLEHEADER: {', '.join(sorted(dh_teams))}")
        print(f"     BTS requires a hit in BOTH games — P(Hit) shown is compound probability")

    # Global top 10 first
    print(f"\n  🏆 GLOBAL TOP 10:")
    print(f"  {'Rk':<4}{'Tier':<6}{'P(Hit)':<9}{'Hitter':<24}{'Tm':<6}"
          f"{'vs Pitcher':<20}{'Tm':<6}{'WHIP':<7}{'BA':<7}{'K%':<7}{'Time':<9}{'Src':<5}")
    print(f"  {'-'*106}")

    for r in recs[:10]:
        ba_str = f"{r['hitter_ba']:.3f}" if r['hitter_ba'] else "  —  "
        k_str = f"{r['hitter_k_pct']:.1%}" if r['hitter_k_pct'] else "  —  "
        whip_str = f"{r['pitcher_whip']:.2f}" if r['pitcher_whip'] else " —  "
        src = "📋" if r.get("has_lineup") else "📊"
        if r.get("is_doubleheader"):
            src += "²"  # flag doubleheader picks
        h_team = r.get("team", "?")[:5]
        p_team = r.get("pitcher_team", "?")[:5]
        gt = r.get("game_time")
        if gt and hasattr(gt, 'total_seconds'):
            total = int(gt.total_seconds())
            hours = total // 3600
            minutes = (total % 3600) // 60
            game_time = f"{hours}:{minutes:02d} ET"
        else:
            game_time = str(gt) if gt else "?"
        print(f"  {r['rank']:<4}"
              f"{r['tier']:<6}"
              f"{r['p_at_least_1']:.1%}    "
              f"{r['hitter_name'][:22]:<24}"
              f"{h_team:<6}"
              f"{r['opp_pitcher'][:18]:<20}"
              f"{p_team:<6}"
              f"{whip_str:<7}"
              f"{ba_str:<7}"
              f"{k_str:<7}"
              f"{game_time:<9}"
              f"{src}")

    print(f"\n  📋 = confirmed lineup    📊 = projected (hitter pool)    ² = doubleheader (compound P)")

    # Then grouped by pitcher
    print(f"\n  {'─'*106}")
    print(f"  DETAIL BY PITCHER (worst → best):")

    recs_by_pitcher = sorted(recs, key=lambda x: float(x.get("pitcher_whip") or 0), reverse=True)

    seen_pitchers = {}
    for r in recs_by_pitcher:
        key = r["opp_pitcher"]
        if key not in seen_pitchers:
            seen_pitchers[key] = []
        if len(seen_pitchers[key]) < 3:
            seen_pitchers[key].append(r)

    for pitcher_name, group in seen_pitchers.items():
        if not group:
            continue
        p = group[0]
        whip_str = f"{p['pitcher_whip']:.2f}" if p['pitcher_whip'] else " —  "
        era_str = f"{p.get('pitcher_era', 0):.2f}" if p.get('pitcher_era') else " —  "
        matched = "✓" if p.get("pitcher_matched") else "~"
        src_label = "📋 lineup" if p.get("has_lineup") else "📊 projected"
        p_team = p.get("pitcher_team", "?")

        print(f"\n  vs {pitcher_name} [{p_team}] (WHIP: {whip_str}, ERA: {era_str}) {matched} {src_label}")
        print(f"  {'Rk':<4}{'Tier':<6}{'P(Hit)':<9}{'Hitter':<24}{'Tm':<6}{'BA':<7}{'K%':<7}{'Time':<9}")
        print(f"  {'-'*4}{'-'*6}{'-'*9}{'-'*24}{'-'*6}{'-'*7}{'-'*7}{'-'*9}")

        for r in group:
            ba_str = f"{r['hitter_ba']:.3f}" if r['hitter_ba'] else "  —  "
            k_str = f"{r['hitter_k_pct']:.1%}" if r['hitter_k_pct'] else "  —  "
            h_team = r.get("team", "?")[:5]
            gt = r.get("game_time")
            if gt and hasattr(gt, 'total_seconds'):
                total = int(gt.total_seconds())
                hours = total // 3600
                minutes = (total % 3600) // 60
                game_time = f"{hours}:{minutes:02d} ET"
            else:
                game_time = str(gt) if gt else "?"

            dh_note = ""
            if r.get("is_doubleheader") and r.get("p_single_game"):
                dh_note = f" (DH: {r['p_single_game']:.0%}×{r['p_other_game']:.0%})" if r.get("p_other_game") else " (DH)"

            print(f"  {r['rank']:<4}"
                  f"{r['tier']:<6}"
                  f"{r['p_at_least_1']:.1%}    "
                  f"{r['hitter_name'][:22]:<24}"
                  f"{h_team:<6}"
                  f"{ba_str:<7}"
                  f"{k_str:<7}"
                  f"{game_time:<9}"
                  f"{dh_note}")

    print(f"\n  ✓ = pitcher stats matched    ~ = using defaults")
    print(f"  📋 = confirmed lineup    📊 = projected (hitter pool)")
    print(f"  ² = doubleheader — P(Hit) is compound: P(Game 1) × P(Game 2)")
    print(f"  Model: v0.4 (BA×pitcher^1.1×K_adj×platoon×contact + talent/streak blend | max 3/pitcher)\n")

def save_recommendations(cursor, recs):
    """Saves the top recommendations to the database for future verification.
    Won't overwrite if picks already exist and any games have started."""
    if not recs:
        return

    target_date = recs[0]['game_date']

    # Check if recommendations already exist for this date
    cursor.execute(
        "SELECT COUNT(*) FROM recommendations WHERE game_date = %s",
        (target_date,)
    )
    existing = cursor.fetchone()[0]

    if existing > 0:
        # Check if any games have gone final or in progress
        cursor.execute("""
            SELECT COUNT(*) FROM games 
            WHERE game_date = %s AND status != 'scheduled'
        """, (target_date,))
        started = cursor.fetchone()[0]

        if started > 0:
            log.info(f"  Preserving {existing} existing picks (games already underway)")
            return

    # Safe to write — either no picks yet or all games still scheduled
    # (prediction_features rows cascade-delete via FK ON DELETE CASCADE)
    cursor.execute(
        "DELETE FROM recommendations WHERE game_date = %s",
        (target_date,)
    )

    rec_query = """
        INSERT INTO recommendations
            (game_pk, game_date, hitter_id, hitter_name, team_abbr,
             opp_pitcher_name, p_at_least_1, tier, `rank`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    feat_query = """
        INSERT INTO prediction_features
            (recommendation_id, game_pk, game_date, hitter_id,
             ba_used, hitter_bats, hitter_k_pct, hitter_pa, games_played, hit_rate_blend,
             pitcher_throws, pitcher_whip, pitcher_era, pitcher_baa, pitcher_k_pct, pitcher_matched,
             batting_order, park_hit_factor, team_factor, has_lineup, is_doubleheader,
             recent_form_games, recent_form_hits, recent_form_streak,
             pitcher_factor, platoon_bonus, contact_bonus, form_bonus, pa_confidence,
             p_hit_per_ab, expected_pa, expected_ab, p_at_least_1, tier, `rank`)
        VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
                %s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s)
    """

    # Individual inserts (not executemany) so we can capture each row's
    # auto-increment id via lastrowid and link the matching feature row to it.
    for r in recs[:10]:
        cursor.execute(rec_query, (
            r['game_pk'], r['game_date'], r['hitter_id'], r['hitter_name'],
            r['team'], r['opp_pitcher'], r['p_at_least_1'], r['tier'], r['rank']
        ))
        rec_id = cursor.lastrowid

        cursor.execute(feat_query, (
            rec_id, r['game_pk'], r['game_date'], r['hitter_id'],
            r.get('ba_used'), r.get('bats'), r.get('hitter_k_pct'), r.get('hitter_pa'),
            r.get('games_played'), r.get('hit_rate_blend'),
            r.get('pitcher_throws'), r.get('pitcher_whip'), r.get('pitcher_era'),
            r.get('pitcher_baa'), r.get('pitcher_k_pct_input'), r.get('pitcher_matched'),
            r.get('batting_order'), r.get('park_hit_factor_used'), r.get('team_factor'),
            r.get('has_lineup'), r.get('is_doubleheader'),
            r.get('recent_form_games'), r.get('recent_form_hits'), r.get('recent_form_streak'),
            r.get('pitcher_factor'), r.get('platoon_bonus'), r.get('contact_bonus'),
            r.get('form_bonus'), r.get('pa_confidence'),
            r.get('p_hit_per_ab'), r.get('expected_pa'), r.get('expected_ab'),
            r['p_at_least_1'], r['tier'], r['rank']
        ))

def apply_first_base_leverage(hitter_stats, opp_1b_stats):
    """
    Adjusts probability based on the hitter's ground-ball tendency 
    and the opposing 1B's defensive quality (Bonus or Penalty).
    """
    gb_rate = hitter_stats.get('gb_pct', 0.45) 
    d_grade = opp_1b_stats.get('defensive_runs_saved', 0) / 10.0
    adjustment = (gb_rate * d_grade) * 0.05 
    return adjustment

def main():
    parser = argparse.ArgumentParser(description="BTS Daily Pick Recommender")
    parser.add_argument(
        "--date", type=str, default=None,
        help="Target date (YYYY-MM-DD). Default: tomorrow"
    )
    parser.add_argument(
        "--top", type=int, default=60,
        help="Number of top picks to show (default: 60)"
    )
    args = parser.parse_args()

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target = date.today() + timedelta(days=1)

    recs = build_recommendations(target, top_n=args.top)

    # Validate pitcher teams, then save and print
    from validate_pitcher_teams import validate_and_fix_pitcher_teams
    conn = get_db()
    cursor = conn.cursor()
    try:
        recs = validate_and_fix_pitcher_teams(recs, conn)
        save_recommendations(cursor, recs)
        conn.commit()
        log.info(f"Saved Top 10 recommendations to the database for {target}")
    finally:
        cursor.close()
        conn.close()

    print_recommendations(recs)

if __name__ == "__main__":
    main()